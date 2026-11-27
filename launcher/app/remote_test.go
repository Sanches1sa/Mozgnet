package main

import "testing"

func TestNormRemoteCloud(t *testing.T) {
	ok := map[string]string{
		"192.168.1.20":               "http://192.168.1.20:8077",
		"192.168.1.20:9000":          "http://192.168.1.20:9000",
		"  http://pc-home.local/ ":   "http://pc-home.local:8077",
		"https://srv.lan:8443/panel": "https://srv.lan:8443",
	}
	for in, want := range ok {
		got, good := normRemoteCloud(in)
		if !good || got != want {
			t.Errorf("%q -> %q,%v want %q", in, got, good, want)
		}
	}
	for _, bad := range []string{"", "   ", "ftp://x", "a b c", "http://", "bad host;rm"} {
		if got, good := normRemoteCloud(bad); good {
			t.Errorf("%q accepted as %q", bad, got)
		}
	}
}
