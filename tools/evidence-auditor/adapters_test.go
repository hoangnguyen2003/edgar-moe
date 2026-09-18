package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

func TestS3ReadOnlyAdapter(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			t.Errorf("unexpected method %s", r.Method)
		}
		if r.URL.Path == "/bucket/missing" {
			w.Header().Set("Content-Type", "application/xml")
			w.WriteHeader(404)
			io.WriteString(w, "<Error><Code>NoSuchKey</Code></Error>")
			return
		}
		if r.URL.Path != "/bucket/evidence" {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		io.WriteString(w, "evidence")
	}))
	defer server.Close()
	client := s3.New(s3.Options{Region: "auto", BaseEndpoint: aws.String(server.URL), UsePathStyle: true, HTTPClient: server.Client(), Credentials: credentials.NewStaticCredentialsProvider("test", "test", "")})
	store := r2Store{client, "bucket"}
	body, err := store.Open(context.Background(), "evidence")
	if err != nil {
		t.Fatal(err)
	}
	data, err := io.ReadAll(body)
	body.Close()
	if err != nil || string(data) != "evidence" {
		t.Fatal(err, string(data))
	}
	if _, err = store.Open(context.Background(), "missing"); !isMissing(err) {
		t.Fatalf("not classified as missing: %v", err)
	}
}
func TestR2Configuration(t *testing.T) {
	for _, endpoint := range []string{"http://example.com", "https://user:secret@example.com", "https://example.com/?secret=x"} {
		if _, err := newR2(endpoint, "bucket", "access", "secret"); err == nil {
			t.Fatal("accepted unsafe configuration")
		}
	}
}
