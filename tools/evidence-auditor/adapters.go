package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/smithy-go"
	_ "github.com/jackc/pgx/v5/stdlib"
)

func isMissing(err error) bool {
	if errors.Is(err, os.ErrNotExist) {
		return true
	}
	var apiError smithy.APIError
	return errors.As(err, &apiError) && (apiError.ErrorCode() == "NoSuchKey" || apiError.ErrorCode() == "NotFound")
}

type localStore struct{ root *os.Root }

func (s localStore) Open(ctx context.Context, key string) (io.ReadCloser, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return s.root.Open(key)
}

type s3Reader interface {
	GetObject(context.Context, *s3.GetObjectInput, ...func(*s3.Options)) (*s3.GetObjectOutput, error)
}
type r2Store struct {
	client s3Reader
	bucket string
}

func (s r2Store) Open(ctx context.Context, key string) (io.ReadCloser, error) {
	result, err := s.client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(s.bucket), Key: aws.String(key)})
	if err != nil {
		return nil, err
	}
	return result.Body, nil
}
func newR2(endpoint, bucket, access, secret string) (r2Store, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || bucket == "" || access == "" || secret == "" {
		return r2Store{}, fmt.Errorf("invalid R2 configuration")
	}
	client := s3.New(s3.Options{Region: "auto", BaseEndpoint: aws.String(endpoint), UsePathStyle: true,
		Credentials: credentials.NewStaticCredentialsProvider(access, secret, ""),
		HTTPClient:  &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
	})
	return r2Store{client, bucket}, nil
}

// Both queries share one consistent, explicitly read-only snapshot. Credentials
// must additionally be SELECT-only: transaction mode is defense in depth.
func loadRegistry(ctx context.Context, dsn string) (Snapshot, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return Snapshot{}, err
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	tx, err := db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true, Isolation: sql.LevelRepeatableRead})
	if err != nil {
		return Snapshot{}, err
	}
	defer tx.Rollback()
	snapshot := Snapshot{Version: 1, Runs: []Run{}, Artifacts: []Artifact{}}
	rows, err := tx.QueryContext(ctx, `SELECT run_id, run_type, status, started_at FROM forward_runs ORDER BY run_id LIMIT 100001`)
	if err != nil {
		return Snapshot{}, err
	}
	for rows.Next() {
		var r Run
		if err = rows.Scan(&r.ID, &r.Type, &r.Status, &r.StartedAt); err != nil {
			rows.Close()
			return Snapshot{}, err
		}
		snapshot.Runs = append(snapshot.Runs, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return Snapshot{}, err
	}
	rows, err = tx.QueryContext(ctx, `SELECT artifact_id, run_id, kind, uri, sha256, size_bytes FROM forward_artifacts ORDER BY artifact_id LIMIT 100001`)
	if err != nil {
		return Snapshot{}, err
	}
	for rows.Next() {
		var a Artifact
		if err = rows.Scan(&a.ID, &a.RunID, &a.Kind, &a.URI, &a.SHA256, &a.Size); err != nil {
			rows.Close()
			return Snapshot{}, err
		}
		snapshot.Artifacts = append(snapshot.Artifacts, a)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return Snapshot{}, err
	}
	if len(snapshot.Runs) > 100000 || len(snapshot.Artifacts) > 100000 {
		return Snapshot{}, fmt.Errorf("registry exceeds audit limit")
	}
	return snapshot, tx.Commit()
}
