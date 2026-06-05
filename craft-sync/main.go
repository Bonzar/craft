// craft-sync — deterministic change detector for the Craft Multi-Document REST API.
//
// Purpose: do the heavy lifting (deep fetch of full block trees + date diffing)
// OUTSIDE the agent. The agent calls this binary with a snapshot of previously
// seen modification times; the binary fetches every block tree over REST, rolls
// modification times up to PAGE granularity (so edits inside nested pages are
// caught — the bug that root-only lastModifiedAt misses), diffs against the
// snapshot, and prints ONLY the changed/new page IDs. The giant JSON never
// touches the agent's context.
//
// Auth: the connect-link token is embedded in the base URL, so no extra header
// is required. Provide the base via --base or the CRAFT_API_BASE env var, e.g.
//   https://connect.craft.do/links/XXXXXXXX/api/v1
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"
)

// Block mirrors the GET /blocks response shape (only the fields we need).
type Block struct {
	ID       string   `json:"id"`
	Type     string   `json:"type"`
	Markdown string   `json:"markdown"`
	Metadata *Meta    `json:"metadata"`
	Content  []Block  `json:"content"`
}

type Meta struct {
	LastModifiedAt string `json:"lastModifiedAt"`
	CreatedAt      string `json:"createdAt"`
}

// Document mirrors a GET /documents item.
type Document struct {
	ID             string `json:"id"`
	Title          string `json:"title"`
	IsDeleted      bool   `json:"isDeleted"`
	LastModifiedAt string `json:"lastModifiedAt"`
}

type docsResponse struct {
	Items []Document `json:"items"`
}

// Page is one change-detection unit (a block of type "page").
type Page struct {
	ID       string    `json:"id"`
	Title    string    `json:"title"`
	ParentID string    `json:"parentId"`
	Path     []string  `json:"path"`
	Eff      time.Time `json:"-"`
}

// Snapshot is the on-disk record of last-seen modification times per page.
type Snapshot struct {
	GeneratedAt string            `json:"generatedAt"`
	Version     int               `json:"version"`
	Pages       map[string]string `json:"pages"`  // pageID -> RFC3339 lastModifiedAt
	Titles      map[string]string `json:"titles"` // pageID -> title (for readability)
}

type ChangedPage struct {
	ID             string   `json:"id"`
	Title          string   `json:"title"`
	Status         string   `json:"status"` // "new" | "changed"
	LastModifiedAt string   `json:"lastModifiedAt"`
	ParentID       string   `json:"parentId,omitempty"`
	Path           []string `json:"path,omitempty"`
}

type Result struct {
	Changed        []ChangedPage `json:"changed"`
	UnchangedCount int           `json:"unchangedCount"`
	TotalPages     int           `json:"totalPages"`
	ScannedDocs    int           `json:"scannedDocs"`
	Errors         []string      `json:"errors,omitempty"`
}

func main() {
	var (
		base       = flag.String("base", os.Getenv("CRAFT_API_BASE"), "Base URL of the Craft connect-link API (or env CRAFT_API_BASE)")
		docsArg    = flag.String("docs", "", "Comma-separated root document IDs to scan. If empty, GET /documents and scan all non-deleted.")
		excludeArg = flag.String("exclude", "", "Comma-separated document/page IDs to skip entirely (e.g. the 'Память для агента' infra docs).")
		snapPath   = flag.String("snapshot", "", "Path to snapshot JSON. Read for diffing; written back when --update-snapshot is set.")
		updateSnap = flag.Bool("update-snapshot", false, "Write the freshly computed snapshot back to --snapshot after diffing.")
		sinceArg   = flag.String("since", "", "RFC3339 fallback: when a page is absent from the snapshot, treat it as changed only if newer than this. Empty => absent pages are 'new'.")
		tree       = flag.Bool("tree", false, "Print changed pages as a nested tree instead of a flat list.")
		timeoutSec = flag.Int("timeout", 60, "Per-request HTTP timeout in seconds.")
		retries    = flag.Int("retries", 3, "HTTP retry attempts per request.")
	)
	flag.Parse()

	if *base == "" {
		fail("missing --base (or CRAFT_API_BASE): the Craft connect-link API base URL")
	}
	*base = strings.TrimRight(*base, "/")

	exclude := toSet(*excludeArg)
	var since time.Time
	if *sinceArg != "" {
		t, err := parseTime(*sinceArg)
		if err != nil {
			fail("invalid --since: %v", err)
		}
		since = t
	}

	snap := Snapshot{Pages: map[string]string{}, Titles: map[string]string{}}
	if *snapPath != "" {
		if b, err := os.ReadFile(*snapPath); err == nil {
			if err := json.Unmarshal(b, &snap); err != nil {
				fail("cannot parse snapshot %s: %v", *snapPath, err)
			}
			if snap.Pages == nil {
				snap.Pages = map[string]string{}
			}
			if snap.Titles == nil {
				snap.Titles = map[string]string{}
			}
		}
	}

	client := &Client{
		http:    &http.Client{Timeout: time.Duration(*timeoutSec) * time.Second},
		base:    *base,
		retries: *retries,
	}

	// Resolve the set of root documents to scan.
	var rootIDs []string
	if *docsArg != "" {
		rootIDs = splitCSV(*docsArg)
	} else {
		docs, err := client.listDocuments()
		if err != nil {
			fail("GET /documents failed: %v", err)
		}
		for _, d := range docs {
			if d.IsDeleted || exclude[d.ID] {
				continue
			}
			rootIDs = append(rootIDs, d.ID)
		}
	}

	pages := map[string]*Page{}
	var scanErrors []string
	scannedDocs := 0

	for _, id := range rootIDs {
		if exclude[id] {
			continue
		}
		root, err := client.getBlocks(id)
		if err != nil {
			scanErrors = append(scanErrors, fmt.Sprintf("%s: %v", id, err))
			continue // skip ghost/broken docs without dying
		}
		scannedDocs++
		collectPages(root, "", nil, exclude, pages)
	}

	res, fresh := Diff(pages, snap, since)
	res.ScannedDocs = scannedDocs
	res.Errors = append(res.Errors, scanErrors...)

	if *updateSnap && *snapPath != "" {
		out, _ := json.MarshalIndent(fresh, "", "  ")
		if err := os.WriteFile(*snapPath, out, 0o644); err != nil {
			fail("cannot write snapshot %s: %v", *snapPath, err)
		}
	}

	if *tree {
		printTree(res, pages)
	} else {
		out, _ := json.MarshalIndent(res, "", "  ")
		fmt.Println(string(out))
	}
}

// Diff compares the freshly scanned pages against the previous snapshot and
// returns the change Result plus the freshly computed snapshot to persist.
// A page is "new" if absent from the snapshot (and, when since is set, newer
// than it), "changed" if its effective time is after the snapshot's value.
func Diff(pages map[string]*Page, snap Snapshot, since time.Time) (Result, Snapshot) {
	res := Result{}
	fresh := Snapshot{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		Version:     snap.Version + 1,
		Pages:       map[string]string{},
		Titles:      map[string]string{},
	}
	for id, p := range pages {
		fresh.Pages[id] = p.Eff.Format(time.RFC3339Nano)
		fresh.Titles[id] = p.Title
	}
	res.TotalPages = len(pages)

	for id, p := range pages {
		prevStr, seen := snap.Pages[id]
		if !seen {
			if !since.IsZero() && !p.Eff.After(since) {
				res.UnchangedCount++
				continue
			}
			res.Changed = append(res.Changed, mkChanged(p, "new"))
			continue
		}
		prev, err := parseTime(prevStr)
		if err != nil || p.Eff.After(prev) {
			res.Changed = append(res.Changed, mkChanged(p, "changed"))
		} else {
			res.UnchangedCount++
		}
	}
	sort.Slice(res.Changed, func(i, j int) bool {
		return res.Changed[i].LastModifiedAt > res.Changed[j].LastModifiedAt
	})
	return res, fresh
}

// collectPages walks a block tree, registering every page-type block as a unit
// whose effective time is the max lastModifiedAt of itself and all descendants
// that are NOT inside a deeper page (those are their own units). This rolls
// non-page edits up to their containing page while attributing nested-page
// edits to the nested page — catching edits the root-only date would miss.
func collectPages(b Block, encPageID string, encPath []string, exclude map[string]bool, pages map[string]*Page) (nonPageMax time.Time) {
	if exclude[b.ID] {
		return time.Time{}
	}
	if b.Type == "page" {
		title := firstLine(b.Markdown)
		path := append(append([]string{}, encPath...), title)
		eff := blockTime(b)
		for _, c := range b.Content {
			eff = maxT(eff, collectPages(c, b.ID, path, exclude, pages))
		}
		pages[b.ID] = &Page{ID: b.ID, Title: title, ParentID: encPageID, Path: encPath, Eff: eff}
		return time.Time{} // a page does not contribute to its enclosing page's non-page max
	}
	m := blockTime(b)
	for _, c := range b.Content {
		m = maxT(m, collectPages(c, encPageID, encPath, exclude, pages))
	}
	return m
}

func mkChanged(p *Page, status string) ChangedPage {
	return ChangedPage{
		ID:             p.ID,
		Title:          p.Title,
		Status:         status,
		LastModifiedAt: p.Eff.Format(time.RFC3339Nano),
		ParentID:       p.ParentID,
		Path:           p.Path,
	}
}

// printTree renders changed pages nested under changed ancestors when possible.
func printTree(res Result, pages map[string]*Page) {
	changed := map[string]ChangedPage{}
	for _, c := range res.Changed {
		changed[c.ID] = c
	}
	children := map[string][]string{}
	var roots []string
	for _, c := range res.Changed {
		if _, parentChanged := changed[c.ParentID]; parentChanged {
			children[c.ParentID] = append(children[c.ParentID], c.ID)
		} else {
			roots = append(roots, c.ID)
		}
	}
	var walk func(id string, depth int)
	walk = func(id string, depth int) {
		c := changed[id]
		fmt.Printf("%s- %s [%s] %s (%s)\n", strings.Repeat("  ", depth), c.Title, c.Status, c.LastModifiedAt, c.ID)
		for _, ch := range children[id] {
			walk(ch, depth+1)
		}
	}
	fmt.Printf("Changed pages: %d  |  unchanged: %d  |  total: %d  |  docs: %d\n",
		len(res.Changed), res.UnchangedCount, res.TotalPages, res.ScannedDocs)
	for _, id := range roots {
		walk(id, 0)
	}
	for _, e := range res.Errors {
		fmt.Printf("! error: %s\n", e)
	}
}

// ---- HTTP client ----

type Client struct {
	http    *http.Client
	base    string
	retries int
}

func (c *Client) getBlocks(id string) (Block, error) {
	u := fmt.Sprintf("%s/blocks?id=%s&maxDepth=-1&fetchMetadata=true", c.base, url.QueryEscape(id))
	var b Block
	err := c.getJSON(u, &b)
	return b, err
}

func (c *Client) listDocuments() ([]Document, error) {
	u := fmt.Sprintf("%s/documents?fetchMetadata=true", c.base)
	var r docsResponse
	err := c.getJSON(u, &r)
	return r.Items, err
}

func (c *Client) getJSON(u string, out any) error {
	var lastErr error
	for attempt := 0; attempt <= c.retries; attempt++ {
		if attempt > 0 {
			time.Sleep(time.Duration(1<<attempt) * time.Second)
		}
		ctx, cancel := context.WithTimeout(context.Background(), c.http.Timeout)
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
		req.Header.Set("Accept", "application/json")
		resp, err := c.http.Do(req)
		if err != nil {
			cancel()
			lastErr = err
			continue
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		cancel()
		if resp.StatusCode == http.StatusNotFound {
			return fmt.Errorf("not found (HTTP 404)")
		}
		if resp.StatusCode >= 400 {
			lastErr = fmt.Errorf("HTTP %d: %s", resp.StatusCode, truncate(string(body), 200))
			if resp.StatusCode >= 500 {
				continue // transient: retry
			}
			return lastErr // 4xx (other than 404): don't hammer
		}
		if err := json.Unmarshal(body, out); err != nil {
			return fmt.Errorf("bad JSON: %v", err)
		}
		return nil
	}
	return lastErr
}

// ---- helpers ----

func blockTime(b Block) time.Time {
	if b.Metadata == nil || b.Metadata.LastModifiedAt == "" {
		return time.Time{}
	}
	t, err := parseTime(b.Metadata.LastModifiedAt)
	if err != nil {
		return time.Time{}
	}
	return t
}

func parseTime(s string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339} {
		if t, err := time.Parse(layout, s); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("unrecognized time %q", s)
}

func maxT(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	return strings.TrimSpace(s)
}

func splitCSV(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func toSet(s string) map[string]bool {
	m := map[string]bool{}
	for _, p := range splitCSV(s) {
		m[p] = true
	}
	return m
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "craft-sync: "+format+"\n", args...)
	os.Exit(1)
}
