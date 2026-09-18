package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"time"
)

func run(args []string, out, errOut io.Writer) int {
	flags := flag.NewFlagSet("evidence-auditor", flag.ContinueOnError)
	flags.SetOutput(errOut)
	manifest := flags.String("manifest", "", "Offline complete registry export (schema_version 1)")
	root := flags.String("local-root", "", "Local evidence root; otherwise use R2")
	timeout := flags.Duration("timeout", 5*time.Minute, "Overall audit deadline")
	stale := flags.Duration("stale-after", 6*time.Hour, "Running job age that requires investigation")
	maxBytes := flags.Int64("max-object-bytes", 64<<20, "Maximum bytes per verified object")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	fail := func(code string) int {
		fmt.Fprintf(out, "{\"schema_version\":1,\"status\":\"incomplete\",\"error_code\":%q}\n", code)
		return 2
	}
	if flags.NArg() != 0 || *timeout <= 0 || *stale <= 0 || *maxBytes <= 0 || *maxBytes > 1<<40 {
		return fail("invalid_arguments")
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	var snapshot Snapshot
	if *manifest != "" {
		file, err := os.Open(*manifest)
		if err != nil {
			return fail("manifest_read_failed")
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() || info.Size() > 32<<20 {
			return fail("manifest_exceeds_limit")
		}
		decoder := json.NewDecoder(io.LimitReader(file, 32<<20))
		decoder.DisallowUnknownFields()
		if err = decoder.Decode(&snapshot); err != nil {
			return fail("invalid_manifest")
		}
		var extra any
		if decoder.Decode(&extra) != io.EOF {
			return fail("invalid_manifest")
		}
		if snapshot.Version != 1 || snapshot.Runs == nil || snapshot.Artifacts == nil {
			return fail("invalid_manifest")
		}
	} else {
		dsn := os.Getenv("AUDITOR_DATABASE_URL")
		if dsn == "" {
			return fail("database_not_configured")
		}
		var err error
		snapshot, err = loadRegistry(ctx, dsn)
		if err != nil {
			return fail("registry_read_failed")
		}
	}
	bucket := os.Getenv("AUDITOR_R2_BUCKET")
	var store ObjectStore
	if *root != "" {
		local, err := os.OpenRoot(*root)
		if err != nil {
			return fail("local_root_unavailable")
		}
		defer local.Close()
		store = localStore{local}
	} else {
		remote, err := newR2(os.Getenv("AUDITOR_R2_ENDPOINT_URL"), bucket, os.Getenv("AUDITOR_R2_ACCESS_KEY_ID"), os.Getenv("AUDITOR_R2_SECRET_ACCESS_KEY"))
		if err != nil {
			return fail("r2_not_configured")
		}
		store = remote
	}
	report, code := audit(ctx, snapshot, store, bucket, time.Now().UTC(), *stale, *maxBytes)
	if err := json.NewEncoder(out).Encode(report); err != nil {
		return 2
	}
	return code
}
func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }
