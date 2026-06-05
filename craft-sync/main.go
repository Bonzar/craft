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
//
//	https://connect.craft.do/links/XXXXXXXX/api/v1
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
	ID       string  `json:"id"`
	Type     string  `json:"type"`
	Markdown string  `json:"markdown"`
	Metadata *Meta   `json:"metadata"`
	Content  []Block `json:"content"`
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
	RootDoc  string    `json:"-"` // root document this page was scanned under
	Eff      time.Time `json:"-"`
}

// Snapshot is the on-disk record of last-seen modification times per page.
//
// Docs/PageDoc enable incremental scans: the cheap GET /documents listing
// reports a doc-level lastModifiedAt that DOES roll up nested-page edits (unlike
// per-block metadata in GET /blocks), so a root doc whose listing date hasn't
// advanced past Docs[id] can be skipped entirely and its pages carried over
// from the previous snapshot — no deep fetch, no block budget spent.
type Snapshot struct {
	GeneratedAt string            `json:"generatedAt"`
	Version     int               `json:"version"`
	Docs        map[string]string `json:"docs,omitempty"`    // rootDocID -> doc-level lastModifiedAt (from /documents)
	Pages       map[string]string `json:"pages"`             // pageID -> RFC3339 lastModifiedAt
	Titles      map[string]string `json:"titles"`            // pageID -> title (for readability)
	PageDoc     map[string]string `json:"pageDoc,omitempty"` // pageID -> rootDocID (for carry-over of skipped docs)
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
	SkippedDocs    int           `json:"skippedDocs"`
	Errors         []string      `json:"errors,omitempty"`
}

func main() {
	var (
		base        = flag.String("base", os.Getenv("CRAFT_API_BASE"), "Base URL of the Craft connect-link API (or env CRAFT_API_BASE)")
		docsArg     = flag.String("docs", "", "Comma-separated root document IDs to scan. If empty, GET /documents and scan all non-deleted.")
		excludeArg  = flag.String("exclude", "", "Comma-separated document/page IDs to skip entirely (e.g. the 'Память для агента' infra docs).")
		snapPath    = flag.String("snapshot", "", "Path to snapshot JSON. Read for diffing; written back when --update-snapshot is set.")
		updateSnap  = flag.Bool("update-snapshot", false, "Write the freshly computed snapshot back to --snapshot after diffing.")
		sinceArg    = flag.String("since", "", "RFC3339 fallback: when a page is absent from the snapshot, treat it as changed only if newer than this. Empty => absent pages are 'new'.")
		tree        = flag.Bool("tree", false, "Print changed pages as a nested tree instead of a flat list.")
		incremental = flag.Bool("incremental", false, "Skip deep-fetching root docs whose /documents listing date hasn't advanced past the snapshot's Docs[id]; carry their pages over. Cuts block-budget usage. No-op without a snapshot that has a docs map.")
		timeoutSec  = flag.Int("timeout", 60, "Per-request HTTP timeout in seconds.")
		retries     = flag.Int("retries", 3, "Retry attempts for transient failures (network / HTTP 5xx).")
		rlRetries   = flag.Int("rl-retries", 5, "Retry attempts for HTTP 429 'Block budget exceeded', with longer exponential backoff (5,10,20,40,60s capped).")
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

	snap := Snapshot{Pages: map[string]string{}, Titles: map[string]string{}, Docs: map[string]string{}, PageDoc: map[string]string{}}
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
			if snap.Docs == nil {
				snap.Docs = map[string]string{}
			}
			if snap.PageDoc == nil {
				snap.PageDoc = map[string]string{}
			}
		}
	}

	client := &Client{
		http:      &http.Client{Timeout: time.Duration(*timeoutSec) * time.Second},
		base:      *base,
		retries:   *retries,
		rlRetries: *rlRetries,
	}

	var scanErrors []string

	// The /documents listing is cheap (metadata only, no block budget) and its
	// doc-level lastModifiedAt rolls up nested-page edits. We use it both to
	// resolve the scan set (when --docs is empty) and to drive incremental skip
	// and the fresh Docs map. Fetch it whenever we need the scan set, want
	// incremental skipping, or are writing a snapshot worth a Docs map.
	var listing []Document
	docDates := map[string]string{}
	needListing := *docsArg == "" || *incremental || (*updateSnap && *snapPath != "")
	if needListing {
		docs, err := client.listDocuments()
		if err != nil {
			if *docsArg == "" {
				fail("GET /documents failed: %v", err) // needed to know what to scan
			}
			scanErrors = append(scanErrors, fmt.Sprintf("listing (for incremental/docs map): %v", err))
			if *incremental {
				*incremental = false // can't skip safely without dates
			}
		}
		listing = docs
		for _, d := range docs {
			docDates[d.ID] = d.LastModifiedAt
		}
	}

	// Resolve the set of root documents to scan.
	var rootIDs []string
	if *docsArg != "" {
		rootIDs = splitCSV(*docsArg)
	} else {
		for _, d := range listing {
			if d.IsDeleted || exclude[d.ID] {
				continue
			}
			rootIDs = append(rootIDs, d.ID)
		}
	}

	pages := map[string]*Page{}
	scannedDocs := 0
	skippedDocs := 0

	for _, id := range rootIDs {
		if exclude[id] {
			continue
		}
		// Incremental skip: doc-level date hasn't advanced -> carry pages over.
		if *incremental && !docChanged(id, docDates[id], snap) {
			carryOver(id, snap, pages)
			skippedDocs++
			continue
		}
		root, err := client.getBlocks(id)
		if err != nil {
			scanErrors = append(scanErrors, fmt.Sprintf("%s: %v", id, err))
			continue // skip ghost/broken docs without dying
		}
		scannedDocs++
		collectPages(root, id, "", nil, exclude, pages)
	}

	res, fresh := Diff(pages, snap, since)
	res.ScannedDocs = scannedDocs
	res.SkippedDocs = skippedDocs
	res.Errors = append(res.Errors, scanErrors...)

	// Record doc-level dates for every root we resolved, so the next run can skip
	// unchanged docs. Prefer the fresh listing date; fall back to the prior value.
	for _, id := range rootIDs {
		if d := docDates[id]; d != "" {
			fresh.Docs[id] = d
		} else if d, ok := snap.Docs[id]; ok {
			fresh.Docs[id] = d
		}
	}

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
		Docs:        map[string]string{},
		Pages:       map[string]string{},
		Titles:      map[string]string{},
		PageDoc:     map[string]string{},
	}
	for id, p := range pages {
		fresh.Pages[id] = p.Eff.Format(time.RFC3339Nano)
		fresh.Titles[id] = p.Title
		if p.RootDoc != "" {
			fresh.PageDoc[id] = p.RootDoc
		}
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
func collectPages(b Block, rootDoc, encPageID string, encPath []string, exclude map[string]bool, pages map[string]*Page) (nonPageMax time.Time) {
	if exclude[b.ID] {
		return time.Time{}
	}
	if b.Type == "page" {
		title := firstLine(b.Markdown)
		path := append(append([]string{}, encPath...), title)
		eff := blockTime(b)
		for _, c := range b.Content {
			eff = maxT(eff, collectPages(c, rootDoc, b.ID, path, exclude, pages))
		}
		pages[b.ID] = &Page{ID: b.ID, Title: title, ParentID: encPageID, Path: encPath, RootDoc: rootDoc, Eff: eff}
		return time.Time{} // a page does not contribute to its enclosing page's non-page max
	}
	m := blockTime(b)
	for _, c := range b.Content {
		m = maxT(m, collectPages(c, rootDoc, encPageID, encPath, exclude, pages))
	}
	return m
}

// docChanged reports whether a root doc must be deep-fetched. It returns true
// (fetch) when we have no recorded doc-level date, no fresh listing date, or the
// listing date is newer than the snapshot's — i.e. we never skip a doc that may
// have changed. The /documents listing date rolls up nested-page edits, so this
// is safe against the cross-page bug that plain root-block dates suffer from.
func docChanged(rootID, listingDate string, snap Snapshot) bool {
	prev, ok := snap.Docs[rootID]
	if !ok || listingDate == "" {
		return true
	}
	pt, e1 := parseTime(prev)
	lt, e2 := parseTime(listingDate)
	if e1 != nil || e2 != nil {
		return true
	}
	return lt.After(pt)
}

// carryOver copies a skipped doc's pages from the previous snapshot into the
// working set unchanged, so they survive into the fresh snapshot and are not
// re-reported. Returns the number of pages carried.
func carryOver(rootID string, snap Snapshot, pages map[string]*Page) int {
	n := 0
	for pid, doc := range snap.PageDoc {
		if doc != rootID {
			continue
		}
		t, _ := parseTime(snap.Pages[pid])
		pages[pid] = &Page{ID: pid, Title: snap.Titles[pid], RootDoc: rootID, Eff: t}
		n++
	}
	return n
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
	fmt.Printf("Changed pages: %d  |  unchanged: %d  |  total: %d  |  scanned: %d  |  skipped: %d\n",
		len(res.Changed), res.UnchangedCount, res.TotalPages, res.ScannedDocs, res.SkippedDocs)
	for _, id := range roots {
		walk(id, 0)
	}
	for _, e := range res.Errors {
		fmt.Printf("! error: %s\n", e)
	}
}

// ---- HTTP client ----

type Client struct {
	http      *http.Client
	base      string
	retries   int // transient (network / 5xx) retry budget
	rlRetries int // HTTP 429 'Block budget exceeded' retry budget (longer backoff)
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
	transientLeft := c.retries // network / 5xx
	rlLeft := c.rlRetries      // HTTP 429 'Block budget exceeded'
	var backoff time.Duration

	for {
		if backoff > 0 {
			time.Sleep(backoff)
			backoff = 0
		}
		ctx, cancel := context.WithTimeout(context.Background(), c.http.Timeout)
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
		req.Header.Set("Accept", "application/json")
		resp, err := c.http.Do(req)
		if err != nil {
			cancel()
			lastErr = err
			if transientLeft <= 0 {
				return lastErr
			}
			backoff = transientBackoff(c.retries - transientLeft)
			transientLeft--
			continue
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		cancel()

		switch {
		case resp.StatusCode == http.StatusNotFound:
			return fmt.Errorf("not found (HTTP 404)")
		case resp.StatusCode == http.StatusTooManyRequests:
			// Block budget exceeded: the window needs time to refill. Wait with a
			// longer backoff and retry rather than dropping the document.
			lastErr = fmt.Errorf("HTTP 429: %s", truncate(string(body), 200))
			if rlLeft <= 0 {
				return lastErr
			}
			backoff = rateLimitBackoff(c.rlRetries - rlLeft)
			rlLeft--
			continue
		case resp.StatusCode >= 500:
			lastErr = fmt.Errorf("HTTP %d: %s", resp.StatusCode, truncate(string(body), 200))
			if transientLeft <= 0 {
				return lastErr
			}
			backoff = transientBackoff(c.retries - transientLeft)
			transientLeft--
			continue
		case resp.StatusCode >= 400:
			return fmt.Errorf("HTTP %d: %s", resp.StatusCode, truncate(string(body), 200)) // other 4xx: don't hammer
		}
		if err := json.Unmarshal(body, out); err != nil {
			return fmt.Errorf("bad JSON: %v", err)
		}
		return nil
	}
}

// transientBackoff: 2s, 4s, 8s, … for network/5xx retries (n is 0-based).
func transientBackoff(n int) time.Duration {
	return time.Duration(1<<uint(n+1)) * time.Second
}

// rateLimitBackoff: 5s, 10s, 20s, 40s, 60s (capped) for HTTP 429, giving the
// block-budget window time to refill (n is 0-based).
func rateLimitBackoff(n int) time.Duration {
	d := time.Duration(5<<uint(n)) * time.Second
	if d > 60*time.Second {
		d = 60 * time.Second
	}
	return d
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
