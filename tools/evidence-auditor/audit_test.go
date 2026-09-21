package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type memoryStore struct {
	data []byte
	err  error
}

func (s memoryStore) Open(context.Context, string) (io.ReadCloser, error) {
	if s.err != nil {
		return nil, s.err
	}
	return io.NopCloser(bytes.NewReader(s.data)), nil
}
func fixture() Snapshot {
	hash := sha256.Sum256([]byte("evidence"))
	digest := hex.EncodeToString(hash[:])
	return Snapshot{Version: 1, Runs: []Run{{ID: "run-1", Type: "forecast", Status: "succeeded", StartedAt: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)}}, Artifacts: []Artifact{{ID: "artifact-1", RunID: "run-1", Kind: "forecast_batch", URI: "local://sha256/" + digest[:2] + "/" + digest + "/forecast-batch.json", SHA256: digest, Size: 8}}}
}
func check(s Snapshot, store ObjectStore) (Report, int) {
	return checkWindow(s, store, 0)
}
func checkWindow(s Snapshot, store ObjectStore, failedRunWindow time.Duration) (Report, int) {
	return audit(context.Background(), s, store, "bucket", time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC), 6*time.Hour, failedRunWindow, 1024)
}
func has(r Report, code string) bool {
	for _, f := range r.Findings {
		if f.Code == code {
			return true
		}
	}
	return false
}
func TestIntegrity(t *testing.T) {
	for _, tc := range []struct {
		name  string
		store memoryStore
		want  string
		exit  int
	}{
		{"valid", memoryStore{data: []byte("evidence")}, "", 0},
		{"missing", memoryStore{err: os.ErrNotExist}, "missing_object", 1},
		{"hash", memoryStore{data: []byte("Evidence")}, "hash_mismatch", 1},
		{"size", memoryStore{data: []byte("evidence-extra")}, "size_mismatch", 1},
		{"access denied", memoryStore{err: errors.New("secret endpoint")}, "object_read_failed", 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r, code := check(fixture(), tc.store)
			if code != tc.exit || (tc.want != "" && !has(r, tc.want)) {
				t.Fatalf("%+v exit %d", r, code)
			}
		})
	}
}
func TestRunReconciliation(t *testing.T) {
	for _, tc := range []struct {
		status, kind string
		age          time.Duration
		missing      bool
	}{
		{"succeeded", "forecast", time.Hour, true}, {"failed", "settlement", time.Hour, true},
		{"running", "forecast", time.Hour, false}, {"running", "forecast", 7 * time.Hour, false},
	} {
		t.Run(tc.status+tc.kind+tc.age.String(), func(t *testing.T) {
			s := fixture()
			s.Artifacts = nil
			s.Runs[0].Status = tc.status
			s.Runs[0].Type = tc.kind
			s.Runs[0].StartedAt = time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC).Add(-tc.age)
			r, _ := check(s, memoryStore{})
			if has(r, "missing_batch_evidence") != tc.missing {
				t.Fatal(r)
			}
		})
	}
}
func TestInvalidReferences(t *testing.T) {
	a := fixture().Artifacts[0]
	for _, uri := range []string{"https://evil.example/object", "local://sha256/../../etc/passwd", a.URI + "?token=secret", strings.Replace(a.URI, "forecast-batch.json", "%2e%2e", 1), strings.Replace(a.URI, "local://", "r2://other/", 1)} {
		a.URI = uri
		if _, err := artifactKey(a, "bucket"); err == nil {
			t.Fatalf("accepted %s", uri)
		}
	}
	a = fixture().Artifacts[0]
	a.URI = strings.Replace(a.URI, "local://", "r2://bucket/", 1)
	if _, err := artifactKey(a, "bucket"); err != nil {
		t.Fatal(err)
	}
}
func TestSymlinkCannotEscapeRoot(t *testing.T) {
	dir := t.TempDir()
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "secret"), []byte("private"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(dir, "link")); err != nil {
		t.Fatal(err)
	}
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	if body, err := (localStore{root}).Open(context.Background(), "link/secret"); err == nil {
		body.Close()
		t.Fatal("escaped local root")
	}
}
func TestLimitsAndIdentity(t *testing.T) {
	s := fixture()
	s.Artifacts[0].Size = 2048
	r, c := check(s, memoryStore{})
	if c != 2 || !has(r, "object_exceeds_audit_limit") {
		t.Fatal(r)
	}
	s = fixture()
	s.Artifacts = append(s.Artifacts, s.Artifacts[0])
	r, _ = check(s, memoryStore{data: []byte("evidence")})
	if !has(r, "invalid_artifact_identity") {
		t.Fatal(r)
	}
	s = fixture()
	s.Artifacts[0].RunID = "unknown"
	r, _ = check(s, memoryStore{data: []byte("evidence")})
	if !has(r, "unknown_run") {
		t.Fatal(r)
	}
}
func TestCLIAndRedaction(t *testing.T) {
	t.Setenv("AUDITOR_DATABASE_URL", "")
	var out bytes.Buffer
	if run(nil, &out, io.Discard) != 2 || !strings.Contains(out.String(), "database_not_configured") {
		t.Fatal(out.String())
	}
	dir := t.TempDir()
	s := fixture()
	key, err := artifactKey(s.Artifacts[0], "")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.MkdirAll(filepath.Dir(filepath.Join(dir, key)), 0700); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, key), []byte("evidence"), 0600); err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(s)
	manifest := filepath.Join(dir, "manifest.json")
	if err = os.WriteFile(manifest, data, 0600); err != nil {
		t.Fatal(err)
	}
	out.Reset()
	if c := run([]string{"--manifest", manifest, "--local-root", dir}, &out, io.Discard); c != 0 {
		t.Fatalf("exit %d %s", c, out.String())
	}
	if strings.Contains(out.String(), dir) {
		t.Fatal("leaked local path")
	}
}

func TestPythonGoldenContract(t *testing.T) {
	data, err := os.ReadFile("testdata/manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	var snapshot Snapshot
	if err = json.Unmarshal(data, &snapshot); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile("testdata/evidence.json")
	if err != nil {
		t.Fatal(err)
	}
	result, code := check(snapshot, memoryStore{data: body})
	if code != 0 || result.Verified != 1 {
		t.Fatal(result)
	}
}

func TestFailedRunWindow(t *testing.T) {
	withFailure := func(started time.Time) Snapshot {
		s := fixture()
		// Failed before writing its batch, as a failed quality gate does.
		s.Runs = append(s.Runs, Run{ID: "run-failed", Type: "forecast", Status: "failed", StartedAt: started})
		return s
	}
	evidence := memoryStore{data: []byte("evidence")}
	old := withFailure(time.Date(2025, 12, 20, 7, 17, 0, 0, time.UTC))

	r, code := check(old, evidence)
	if code != 1 || !has(r, "failed_run") || !has(r, "missing_batch_evidence") || r.HistoricalFailedRuns != 0 {
		t.Fatalf("default window must report every failed run: %+v exit %d", r, code)
	}

	r, code = checkWindow(old, evidence, 12*time.Hour)
	if code != 0 || r.Status != "passed" || len(r.Findings) != 0 || r.HistoricalFailedRuns != 1 || r.Verified != 1 {
		t.Fatalf("an old failure must not fail every later audit: %+v exit %d", r, code)
	}

	recent := withFailure(time.Date(2026, 1, 1, 23, 0, 0, 0, time.UTC))
	r, code = checkWindow(recent, evidence, 12*time.Hour)
	if code != 1 || !has(r, "failed_run") || r.HistoricalFailedRuns != 0 {
		t.Fatalf("a failure inside the window must still be reported: %+v exit %d", r, code)
	}

	r, code = checkWindow(old, memoryStore{data: []byte("tampered")}, 12*time.Hour)
	if code != 1 || !has(r, "hash_mismatch") {
		t.Fatalf("the window must not suppress integrity findings: %+v exit %d", r, code)
	}
}

func TestHistoricalFailedRunsAreOmittedWhenZero(t *testing.T) {
	r, _ := check(fixture(), memoryStore{data: []byte("evidence")})
	data, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "historical_failed_runs") {
		t.Fatalf("report schema changed without the window: %s", data)
	}
}

func TestNegativeFailedRunWindowIsRejected(t *testing.T) {
	var out bytes.Buffer
	if run([]string{"--failed-run-window", "-1h"}, &out, io.Discard) != 2 || !strings.Contains(out.String(), "invalid_arguments") {
		t.Fatal(out.String())
	}
}
