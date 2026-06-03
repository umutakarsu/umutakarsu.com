#!/usr/bin/env python3
"""Static server with .html fallback (mimics .htaccess clean URLs)."""
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

DIR = "/Users/umutakarsu/umutakarsu.com"
os.chdir(DIR)

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        fs_path = os.path.join(DIR, path.lstrip("/"))
        if path != "/" and not os.path.exists(fs_path) and not path.endswith("/"):
            html_path = fs_path + ".html"
            if os.path.exists(html_path):
                self.path = path + ".html"
        return super().do_GET()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4321
    HTTPServer(("", port), Handler).serve_forever()
