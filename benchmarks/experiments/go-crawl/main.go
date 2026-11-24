// Experiment: the benchmark crawl written in Go, to measure how much of the
// time is Python itself. It covers only what the benchmark task needs
// (robots.txt, path prefixes, one level of links) and is not the crawler.
package main

import (
	"bufio"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"golang.org/x/net/html"
)

const userAgent = "askUZH-TaxonomyCrawler/0.1 (+local; research)"

type scope struct {
	host     string
	prefixes []string
}

func bare(h string) string { return strings.TrimPrefix(strings.ToLower(h), "www.") }

func (s scope) in(u *url.URL) bool {
	if bare(u.Hostname()) != s.host {
		return false
	}
	for _, p := range s.prefixes {
		root := "/" + strings.Trim(p, "/")
		if u.Path == root || strings.HasPrefix(u.Path, root+"/") {
			return true
		}
	}
	return len(s.prefixes) == 0
}

func normalize(base *url.URL, href string) *url.URL {
	href = strings.TrimSpace(href)
	low := strings.ToLower(href)
	if href == "" || strings.HasPrefix(low, "#") || strings.HasPrefix(low, "mailto:") ||
		strings.HasPrefix(low, "tel:") || strings.HasPrefix(low, "javascript:") || strings.Contains(href, "[%") {
		return nil
	}
	u, err := base.Parse(href)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") {
		return nil
	}
	u.Fragment = ""
	u.Host = strings.ToLower(u.Host)
	if u.Path == "" {
		u.Path = "/"
	}
	return u
}

func get(client *http.Client, target string) ([]byte, string, error) {
	req, _ := http.NewRequest("GET", target, nil)
	req.Header.Set("User-Agent", userAgent)
	resp, err := client.Do(req)
	if err != nil {
		return nil, "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(resp.Body)
	return body, resp.Header.Get("Content-Type"), err
}

func disallowed(robots string) []string {
	var rules []string
	applies := false
	sc := bufio.NewScanner(strings.NewReader(robots))
	for sc.Scan() {
		key, value, ok := strings.Cut(sc.Text(), ":")
		if !ok {
			continue
		}
		key, value = strings.ToLower(strings.TrimSpace(key)), strings.TrimSpace(value)
		if key == "user-agent" {
			applies = value == "*"
		} else if key == "disallow" && applies && value != "" {
			rules = append(rules, value)
		}
	}
	return rules
}

func links(body []byte, base *url.URL) []*url.URL {
	var out []*url.URL
	z := html.NewTokenizer(strings.NewReader(string(body)))
	for {
		tt := z.Next()
		if tt == html.ErrorToken {
			return out
		}
		if tt != html.StartTagToken && tt != html.SelfClosingTagToken {
			continue
		}
		name, hasAttr := z.TagName()
		if len(name) != 1 || name[0] != 'a' {
			continue
		}
		for hasAttr {
			var key, val []byte
			key, val, hasAttr = z.TagAttr()
			if string(key) == "href" {
				if u := normalize(base, string(val)); u != nil {
					out = append(out, u)
				}
			}
		}
	}
}

func main() {
	args := os.Args[1:]
	var seed, out string
	sc := scope{}
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--include-prefix":
			sc.prefixes = append(sc.prefixes, args[i+2])
			i += 2
		case "--out":
			out = args[i+1]
			i++
		case "--max-depth", "--max-pages":
			i++
		case "crawl", "--no-sitemap":
		default:
			seed = args[i]
		}
	}
	if !strings.Contains(seed, "://") {
		seed = "https://" + seed
	}
	start, _ := url.Parse(seed)
	sc.host = bare(start.Hostname())
	client := &http.Client{}

	robots, _, _ := get(client, start.Scheme+"://"+start.Host+"/robots.txt")
	rules := disallowed(string(robots))
	allowed := func(u *url.URL) bool {
		for _, r := range rules {
			if strings.HasPrefix(u.Path, r) {
				return false
			}
		}
		return true
	}

	seen := map[string]bool{start.String(): true}
	body, _, err := get(client, start.String())
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	var children []*url.URL
	for _, u := range links(body, start) {
		if sc.in(u) && allowed(u) && !seen[u.String()] {
			seen[u.String()] = true
			children = append(children, u)
		}
	}
	// Depth 1: each child is read, as the Python crawl does, and its links are not followed.
	var wg sync.WaitGroup
	for _, u := range children {
		wg.Add(1)
		go func(u *url.URL) {
			defer wg.Done()
			if page, _, err := get(client, u.String()); err == nil {
				links(page, u)
			}
		}(u)
	}
	wg.Wait()

	urls := make([]string, 0, len(seen))
	for u := range seen {
		urls = append(urls, u)
	}
	sort.Strings(urls)
	os.MkdirAll(out, 0o755)
	os.WriteFile(filepath.Join(out, "urls.txt"), []byte(strings.Join(urls, "\n")+"\n"), 0o644)
}
