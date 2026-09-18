package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/url"
	"regexp"
	"strings"
	"time"
)

type Artifact struct {
	ID     string `json:"artifact_id"`
	RunID  string `json:"run_id"`
	Kind   string `json:"kind"`
	URI    string `json:"uri"`
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size_bytes"`
}
type Run struct {
	ID        string    `json:"run_id"`
	Type      string    `json:"run_type"`
	Status    string    `json:"status"`
	StartedAt time.Time `json:"started_at"`
}
type Snapshot struct {
	Version   int        `json:"schema_version"`
	Runs      []Run      `json:"runs"`
	Artifacts []Artifact `json:"artifacts"`
}
type Finding struct {
	Code       string `json:"code"`
	RunID      string `json:"run_id,omitempty"`
	ArtifactID string `json:"artifact_id,omitempty"`
}
type Report struct {
	Version   int       `json:"schema_version"`
	AuditedAt time.Time `json:"audited_at"`
	Status    string    `json:"status"`
	Runs      int       `json:"runs_seen"`
	Artifacts int       `json:"artifacts_seen"`
	Verified  int       `json:"objects_verified"`
	Findings  []Finding `json:"findings"`
}
type ObjectStore interface {
	Open(context.Context, string) (io.ReadCloser, error)
}

var digestPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

// The URI is an identity, never an arbitrary network download destination.
func artifactKey(a Artifact, bucket string) (string, error) {
	u, err := url.Parse(a.URI)
	if err != nil || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" {
		return "", fmt.Errorf("invalid artifact identity")
	}
	var key string
	switch u.Scheme {
	case "local":
		key = u.Host + u.Path
	case "r2":
		if bucket == "" || u.Host != bucket {
			return "", fmt.Errorf("bucket mismatch")
		}
		key = strings.TrimPrefix(u.Path, "/")
	default:
		return "", fmt.Errorf("unsupported artifact scheme")
	}
	parts := strings.Split(key, "/")
	if !digestPattern.MatchString(a.SHA256) || len(parts) != 4 || parts[0] != "sha256" || parts[1] != a.SHA256[:2] || parts[2] != a.SHA256 || parts[3] == "" || parts[3] == "." || parts[3] == ".." || strings.ContainsAny(parts[3], "\\\x00") || a.Size < 0 {
		return "", fmt.Errorf("invalid content-addressed key")
	}
	return key, nil
}

func audit(ctx context.Context, snapshot Snapshot, store ObjectStore, bucket string, now time.Time, staleAfter time.Duration, maxBytes int64) (Report, int) {
	report := Report{Version: 1, AuditedAt: now.UTC(), Status: "passed", Runs: len(snapshot.Runs), Artifacts: len(snapshot.Artifacts), Findings: []Finding{}}
	code := 0
	add := func(kind, run, id string, operational bool) {
		report.Findings = append(report.Findings, Finding{kind, run, id})
		if code == 0 {
			code = 1
		}
		if operational {
			code = 2
		}
	}
	runs := map[string]Run{}
	batches := map[string]map[string]bool{}
	for _, r := range snapshot.Runs {
		if _, ok := runs[r.ID]; ok || r.ID == "" {
			add("invalid_run_identity", r.ID, "", false)
		}
		runs[r.ID] = r
	}
	seen := map[string]bool{}
	for _, a := range snapshot.Artifacts {
		if seen[a.ID] || a.ID == "" {
			add("invalid_artifact_identity", a.RunID, a.ID, false)
			continue
		}
		seen[a.ID] = true
		if _, ok := runs[a.RunID]; !ok {
			add("unknown_run", a.RunID, a.ID, false)
		}
		if batches[a.RunID] == nil {
			batches[a.RunID] = map[string]bool{}
		}
		batches[a.RunID][a.Kind] = true
		key, err := artifactKey(a, bucket)
		if err != nil {
			add("invalid_artifact_reference", a.RunID, a.ID, false)
			continue
		}
		if a.Size > maxBytes {
			add("object_exceeds_audit_limit", a.RunID, a.ID, true)
			continue
		}
		body, err := store.Open(ctx, key)
		if err != nil {
			if isMissing(err) {
				add("missing_object", a.RunID, a.ID, false)
			} else {
				add("object_read_failed", a.RunID, a.ID, true)
			}
			continue
		}
		hash := sha256.New()
		n, err := io.Copy(hash, io.LimitReader(body, a.Size+1))
		closeErr := body.Close()
		if err != nil || closeErr != nil {
			add("object_read_failed", a.RunID, a.ID, true)
			continue
		}
		if n != a.Size {
			add("size_mismatch", a.RunID, a.ID, false)
			continue
		}
		if hex.EncodeToString(hash.Sum(nil)) != a.SHA256 {
			add("hash_mismatch", a.RunID, a.ID, false)
			continue
		}
		report.Verified++
	}
	for _, r := range snapshot.Runs {
		completed := r.Status == "succeeded" || r.Status == "failed"
		if r.Status == "failed" {
			add("failed_run", r.ID, "", false)
		}
		if r.StartedAt.IsZero() || r.StartedAt.After(now) {
			add("invalid_run_timestamp", r.ID, "", false)
		}
		if r.Status == "running" && now.Sub(r.StartedAt) > staleAfter {
			add("stale_run", r.ID, "", false)
		}
		if r.Status != "running" && !completed {
			add("unknown_run_status", r.ID, "", false)
		}
		expected := ""
		switch r.Type {
		case "forecast":
			expected = "forecast_batch"
		case "settlement":
			expected = "settlement_batch"
		}
		if completed && expected != "" && !batches[r.ID][expected] {
			add("missing_batch_evidence", r.ID, "", false)
		}
	}
	if code == 1 {
		report.Status = "findings"
	}
	if code == 2 {
		report.Status = "incomplete"
	}
	return report, code
}
