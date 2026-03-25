"""
CI SEO Agent — WordPress Implementer
Executes SEO action items on the live WordPress site via:
  1. WP-CLI (primary, most reliable)
  2. WordPress REST API (fallback / remote)
"""
import json
import logging
import subprocess
import re
from typing import Optional
from urllib.parse import urlparse
import requests
from requests.auth import HTTPBasicAuth

from config import cfg
from analyzer import analyzer

logger = logging.getLogger("ci.implementer")


class WordPressClient:
    """Thin wrapper around WP REST API + WP-CLI."""

    def __init__(self):
        self.base_url = cfg.WP_BASE_URL
        self.auth = HTTPBasicAuth(cfg.WP_USER, cfg.WP_APP_PASSWORD)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers["User-Agent"] = "CI-SEO-Agent/1.0"

    # ── WP-CLI ─────────────────────────────────────────────────────────────

    def wp_cli(self, *args, timeout: int = 30) -> tuple[str, str, int]:
        """Run a WP-CLI command. Returns (stdout, stderr, returncode)."""
        cmd = [
            cfg.WP_CLI_PATH,
            "--allow-root",
            f"--path={cfg.WP_ROOT}",
            *args,
        ]
        logger.debug(f"WP-CLI: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode

    def get_post_by_url(self, url: str) -> Optional[dict]:
        """Find a WordPress post/page by URL using deterministic path resolution."""
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        path_segments = [segment for segment in path.split("/") if segment]
        slug = path_segments[-1] if path_segments else "home"
        post_type_hint = path_segments[0] if len(path_segments) > 1 else ""

        php_code = (
            "$url = {url}; "
            "$path = trim((string) parse_url($url, PHP_URL_PATH), '/'); "
            "$segments = array_values(array_filter(explode('/', $path))); "
            "$slug = $segments ? end($segments) : 'home'; "
            "$hint = count($segments) > 1 ? $segments[0] : ''; "
            "$post = null; "
            "$post_id = url_to_postid($url); "
            "if ($post_id) { $post = get_post($post_id); } "
            "if (!$post && $path) { "
            "  $types = ['page', 'post', 'product']; "
            "  if ($hint === 'product') { $types = ['product', 'page', 'post']; } "
            "  foreach ($types as $type) { "
            "    $candidate = get_page_by_path($path, OBJECT, $type); "
            "    if (!$candidate) { $candidate = get_page_by_path($slug, OBJECT, $type); } "
            "    if ($candidate && $candidate->post_status === 'publish') { $post = $candidate; break; } "
            "  } "
            "} "
            "if (!$post && $slug) { "
            "  $types = ['page', 'post', 'product']; "
            "  if ($hint === 'product') { $types = ['product', 'page', 'post']; } "
            "  $query = new WP_Query(["
            "    'name' => $slug,"
            "    'post_type' => $types,"
            "    'post_status' => 'publish',"
            "    'posts_per_page' => 5,"
            "    'no_found_rows' => true,"
            "    'fields' => 'ids'"
            "  ]); "
            "  foreach ((array) $query->posts as $candidate_id) { "
            "    $candidate = get_post($candidate_id); "
            "    if ($candidate && untrailingslashit(get_permalink($candidate)) === untrailingslashit($url)) { "
            "      $post = $candidate; "
            "      break; "
            "    } "
            "  } "
            "} "
            "if (!$post) { echo ''; return; } "
            "echo wp_json_encode(["
            "  'ID' => (int) $post->ID,"
            "  'post_title' => (string) get_the_title($post),"
            "  'post_type' => (string) $post->post_type,"
            "  'guid' => (string) get_permalink($post)"
            "]);"
        ).replace("{url}", json.dumps(url))

        stdout, _, rc = self.wp_cli("eval", php_code, timeout=45)
        if rc == 0 and stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                logger.warning(f"Unexpected post lookup output for {url}: {stdout[:200]}")

        # Try REST API fallback
        try:
            for post_type in ["posts", "pages"]:
                resp = self.session.get(
                    f"{self.base_url}/{post_type}",
                    params={"slug": slug, "_fields": "id,title,type,link"},
                    timeout=10,
                )
                if resp.ok and resp.json():
                    p = resp.json()[0]
                    return {
                        "ID": p["id"],
                        "post_title": p["title"]["rendered"],
                        "post_type": p.get("type", ""),
                        "guid": p["link"],
                    }
        except requests.RequestException as exc:
            logger.warning(f"REST API fallback failed: {exc}")

        return None

    def get_post_by_id(self, post_id: int) -> Optional[dict]:
        """Get post data by ID."""
        stdout, _, rc = self.wp_cli(
            "post", "get", str(post_id),
            "--fields=ID,post_title,post_content,post_type,post_status",
            "--format=json",
        )
        if rc == 0 and stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                pass
        return None

    def get_rank_math_meta(self, post_id: int) -> dict:
        """Get Rank Math SEO meta for a post."""
        stdout, _, rc = self.wp_cli(
            "eval",
            f"echo json_encode(["
            f"'title' => get_post_meta({post_id}, 'rank_math_title', true),"
            f"'description' => get_post_meta({post_id}, 'rank_math_description', true),"
            f"'focus_keyword' => get_post_meta({post_id}, 'rank_math_focus_keyword', true),"
            f"'canonical' => get_post_meta({post_id}, 'rank_math_canonical_url', true)"
            f"]);"
        )
        if rc == 0 and stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                pass
        return {}

    def update_rank_math_meta(
        self,
        post_id: int,
        title: str = None,
        description: str = None,
        focus_keyword: str = None,
        canonical: str = None,
    ) -> bool:
        """Update Rank Math SEO meta fields for a post."""
        updates = []
        if title:
            updates.append(
                f"update_post_meta({post_id}, 'rank_math_title', '{self._esc(title)}');"
            )
        if description:
            updates.append(
                f"update_post_meta({post_id}, 'rank_math_description', '{self._esc(description)}');"
            )
        if focus_keyword:
            updates.append(
                f"update_post_meta({post_id}, 'rank_math_focus_keyword', '{self._esc(focus_keyword)}');"
            )
        if canonical:
            updates.append(
                f"update_post_meta({post_id}, 'rank_math_canonical_url', '{self._esc(canonical)}');"
            )

        if not updates:
            return True

        php_code = " ".join(updates)
        _, stderr, rc = self.wp_cli("eval", php_code)
        if rc != 0:
            logger.error(f"Rank Math meta update failed for {post_id}: {stderr}")
            return False
        logger.info(f"Rank Math meta updated for post {post_id}")
        return True

    def update_post_title(self, post_id: int, title: str) -> bool:
        """Update post title."""
        _, stderr, rc = self.wp_cli(
            "post", "update", str(post_id),
            f"--post_title={title}",
        )
        if rc != 0:
            logger.error(f"Post title update failed for {post_id}: {stderr}")
            return False
        return True

    def purge_cache(self) -> bool:
        """Purge LiteSpeed Cache after changes."""
        _, _, rc = self.wp_cli("litespeed-purge", "all")
        if rc == 0:
            logger.info("LiteSpeed cache purged")
            return True
        # Try alternative cache plugins
        self.wp_cli("w3-total-cache", "flush", "all")
        self.wp_cli("cache", "flush")
        return True

    def _esc(self, s: str) -> str:
        """Escape string for PHP single-quoted context."""
        return s.replace("\\", "\\\\").replace("'", "\\'")


class Implementer:
    def __init__(self):
        self.wp = WordPressClient()

    def execute_action(self, action: dict) -> tuple[bool, str]:
        """
        Execute a single action item.
        Returns (success, result_message).
        """
        meta = action.get("metadata", action)
        action_type = meta.get("action_type", "")
        target_url = meta.get("target_url", "")
        target_keyword = meta.get("target_keyword", "")
        impl_data = meta.get("implementation_data", {})
        if isinstance(impl_data, str):
            try:
                impl_data = json.loads(impl_data)
            except Exception:
                impl_data = {}

        logger.info(f"Executing {action_type}: {target_url or target_keyword}")

        try:
            if action_type == "UPDATE_META_DESCRIPTION":
                return self._update_meta_description(target_url, target_keyword, impl_data)
            elif action_type == "UPDATE_PAGE_TITLE":
                return self._update_page_title(target_url, target_keyword, impl_data)
            elif action_type == "ADD_INTERNAL_LINK":
                return self._add_internal_link(target_url, impl_data)
            elif action_type == "FIX_CANONICAL":
                return self._fix_canonical(target_url, impl_data)
            elif action_type == "UPDATE_SCHEMA":
                return self._update_schema(target_url, impl_data)
            elif action_type == "OPTIMIZE_HEADING":
                return self._optimize_heading(target_url, impl_data)
            elif action_type == "CREATE_CONTENT_BRIEF":
                return self._create_content_brief(target_url, target_keyword, impl_data)
            elif action_type == "FLAG_FOR_REVIEW":
                return True, f"Flagged for manual review: {meta.get('description', '')}"
            else:
                return False, f"Unknown action type: {action_type}"
        except Exception as e:
            logger.error(f"Action execution error ({action_type}): {e}", exc_info=True)
            return False, f"Error: {str(e)}"

    # ── Action Implementations ─────────────────────────────────────────────

    def _update_meta_description(
        self, url: str, keyword: str, impl_data: dict
    ) -> tuple[bool, str]:
        """Update Rank Math meta description for a page."""
        new_desc = impl_data.get("new_value", "")

        # If no new description provided, generate one
        if not new_desc and url:
            current_desc = impl_data.get("current_value", "")
            logger.info(f"Generating meta description for {url}")
            new_desc = analyzer.generate_meta_description(url, keyword, current_desc)

        if not new_desc:
            return False, "Could not determine new meta description"

        # Find the post
        post = self.wp.get_post_by_url(url)
        if not post:
            return False, f"Post not found for URL: {url}"

        post_id = post.get("ID") or post.get("id")
        if not post_id:
            return False, "Could not determine post ID"

        # Get current meta for logging
        current = self.wp.get_rank_math_meta(int(post_id))
        old_desc = current.get("description", "")

        # Apply update
        ok = self.wp.update_rank_math_meta(
            post_id=int(post_id),
            description=new_desc,
            focus_keyword=keyword or current.get("focus_keyword"),
        )

        if ok:
            self.wp.purge_cache()
            return True, (
                f"Meta description updated for post {post_id}\n"
                f"Old: {old_desc[:80]}...\n"
                f"New: {new_desc[:80]}..."
            )
        return False, f"Failed to update meta description for post {post_id}"

    def _update_page_title(
        self, url: str, keyword: str, impl_data: dict
    ) -> tuple[bool, str]:
        """Update Rank Math SEO title (not post title) for a page."""
        new_title = impl_data.get("new_value", "")

        if not new_title and url:
            current_title = impl_data.get("current_value", "")
            new_title = analyzer.generate_page_title(url, keyword, current_title)

        if not new_title:
            return False, "Could not determine new page title"

        post = self.wp.get_post_by_url(url)
        if not post:
            return False, f"Post not found for URL: {url}"

        post_id = post.get("ID") or post.get("id")
        current = self.wp.get_rank_math_meta(int(post_id))
        old_title = current.get("title", "")

        ok = self.wp.update_rank_math_meta(
            post_id=int(post_id),
            title=new_title,
            focus_keyword=keyword or current.get("focus_keyword"),
        )
        if ok:
            self.wp.purge_cache()
            return True, (
                f"SEO title updated for post {post_id}\n"
                f"Old: {old_title}\n"
                f"New: {new_title}"
            )
        return False, f"Failed to update title for post {post_id}"

    def _fix_canonical(self, url: str, impl_data: dict) -> tuple[bool, str]:
        """Fix canonical URL for a page."""
        canonical_url = impl_data.get("new_value", url)
        post = self.wp.get_post_by_url(url)
        if not post:
            return False, f"Post not found: {url}"

        post_id = post.get("ID") or post.get("id")
        ok = self.wp.update_rank_math_meta(
            post_id=int(post_id),
            canonical=canonical_url,
        )
        if ok:
            self.wp.purge_cache()
            return True, f"Canonical set to {canonical_url} for post {post_id}"
        return False, "Failed to update canonical"

    def _add_internal_link(self, url: str, impl_data: dict) -> tuple[bool, str]:
        """
        Inject an internal link into the source post content.

        impl_data keys:
          from_url    — page to edit (defaults to url)
          to_url      — destination URL to link to
          anchor_text — exact plain text in the content to turn into a link
          notes       — context description
        """
        from_url = impl_data.get("from_url") or url
        to_url = impl_data.get("to_url", "")
        anchor = impl_data.get("anchor_text", "").strip()

        if not from_url or not to_url or not anchor:
            return False, (
                f"ADD_INTERNAL_LINK missing required fields — "
                f"from_url={bool(from_url)}, to_url={bool(to_url)}, anchor_text={bool(anchor)}"
            )

        # Resolve post
        post = self.wp.get_post_by_url(from_url)
        if not post:
            return False, f"Source post not found for URL: {from_url}"
        post_id = int(post.get("ID") or post.get("id", 0))
        if not post_id:
            return False, "Could not determine post ID for source URL"

        # PHP: find first plain-text occurrence of anchor not already inside an <a> tag.
        # Strategy: split content on <a ...>...</a> blocks, replace only in text segments.
        php = (
            f"$post = get_post({post_id}); "
            f"if (!$post) {{ echo 'NOT_FOUND'; return; }} "
            f"$content = $post->post_content; "
            f"$anchor  = {json.dumps(anchor)}; "
            f"$href    = {json.dumps(to_url)}; "
            # Split into parts: text nodes and <a>...</a> blocks alternately
            f"$parts = preg_split('/(<a\\b[^>]*>.*?<\\/a>)/is', $content, -1, PREG_SPLIT_DELIM_CAPTURE); "
            f"$replaced = 0; "
            f"$new_parts = []; "
            f"foreach ($parts as $part) {{ "
            f"  if ($replaced === 0 && stripos($part, '<a ') !== 0 && stripos($part, $anchor) !== false) {{ "
            f"    $pos = stripos($part, $anchor); "
            f"    $part = substr($part, 0, $pos) "
            f"          . '<a href=\"' . esc_url($href) . '\">' . esc_html($anchor) . '</a>' "
            f"          . substr($part, $pos + strlen($anchor)); "
            f"    $replaced++; "
            f"  }} "
            f"  $new_parts[] = $part; "
            f"}} "
            f"if ($replaced === 0) {{ echo 'NOT_FOUND_IN_CONTENT'; return; }} "
            f"wp_update_post(['ID' => {post_id}, 'post_content' => implode('', $new_parts)]); "
            f"echo 'OK:' . $replaced;"
        )

        stdout, stderr, rc = self.wp.wp_cli("eval", php, timeout=45)
        # LiteSpeed and other plugins may print to stdout before our echo — find our marker
        result_line = next(
            (l for l in stdout.splitlines() if l.startswith(("OK:", "NOT_FOUND", "ERROR:"))),
            ""
        )
        if rc != 0:
            return False, f"WP-CLI error: {stderr[:300]}"
        if result_line.startswith("NOT_FOUND_IN_CONTENT"):
            return False, (
                f"Anchor text '{anchor}' not found as plain text in post {post_id} content. "
                f"It may already be linked or not present."
            )
        if result_line.startswith("NOT_FOUND"):
            return False, f"Post {post_id} not found during PHP eval"
        if not result_line.startswith("OK:"):
            return False, f"Unexpected output from link injection: {stdout[:200]}"

        self.wp.purge_cache()
        logger.info(f"Internal link injected: '{anchor}' → {to_url} in post {post_id}")
        return True, (
            f"Internal link added in post {post_id}\n"
            f"  Anchor: '{anchor}'\n"
            f"  → {to_url}\n"
            f"  Source: {from_url}"
        )

    def _update_schema(self, url: str, impl_data: dict) -> tuple[bool, str]:
        """
        Inject custom JSON-LD schema for a specific post via `indg_custom_schema_json` postmeta.

        The MU plugin `indg-seo-runtime-fixes.php` reads this postmeta on singular pages
        and outputs a <script type="application/ld+json"> block in <head>.

        impl_data keys:
          schema_type   — e.g. "FAQPage", "HowTo", "VideoObject"
          schema_json   — full JSON-LD object (dict) or list of objects
          notes         — description of what's being added
        """
        post = self.wp.get_post_by_url(url)
        if not post:
            return False, f"Post not found for URL: {url}"
        post_id = int(post.get("ID") or post.get("id", 0))
        if not post_id:
            return False, "Could not determine post ID"

        schema_json = impl_data.get("schema_json")
        schema_type = impl_data.get("schema_type", "")
        notes = impl_data.get("notes", "")

        # Build schema if raw JSON not provided but type+notes present
        if not schema_json and schema_type == "FAQPage":
            faqs = impl_data.get("faqs", [])
            if faqs:
                schema_json = {
                    "@context": "https://schema.org",
                    "@type": "FAQPage",
                    "mainEntity": [
                        {
                            "@type": "Question",
                            "name": faq.get("question", ""),
                            "acceptedAnswer": {
                                "@type": "Answer",
                                "text": faq.get("answer", ""),
                            },
                        }
                        for faq in faqs
                        if faq.get("question") and faq.get("answer")
                    ],
                }

        if not schema_json:
            return False, (
                f"UPDATE_SCHEMA for post {post_id}: no schema_json provided and could not build one. "
                f"Notes: {notes}"
            )

        # Merge with any existing custom schema
        existing_raw = None
        stdout, _, rc = self.wp.wp_cli(
            "eval",
            f"echo get_post_meta({post_id}, 'indg_custom_schema_json', true);"
        )
        # Strip any plugin stdout noise; the JSON starts with '[' or '{'
        if rc == 0 and stdout:
            json_start = next(
                (i for i, c in enumerate(stdout) if c in ("{", "[")), -1
            )
            existing_raw = stdout[json_start:] if json_start >= 0 else None

        existing_schemas: list = []
        if existing_raw:
            try:
                parsed = json.loads(existing_raw)
                existing_schemas = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                pass

        # Add/replace schema of same type
        new_node = schema_json if isinstance(schema_json, dict) else schema_json
        new_type = (new_node.get("@type", "") if isinstance(new_node, dict) else "")
        merged = [s for s in existing_schemas if s.get("@type") != new_type]
        if isinstance(new_node, list):
            merged.extend(new_node)
        else:
            merged.append(new_node)

        schema_str = json.dumps(merged, ensure_ascii=False)

        _, stderr, rc = self.wp.wp_cli(
            "eval",
            f"update_post_meta({post_id}, 'indg_custom_schema_json', {json.dumps(schema_str)});"
        )
        if rc != 0:
            return False, f"Failed to save schema to postmeta: {stderr[:300]}"

        self.wp.purge_cache()
        logger.info(f"Custom schema ({new_type or 'unknown'}) saved for post {post_id}")
        return True, (
            f"Schema updated for post {post_id} ({url})\n"
            f"  Type: {new_type or 'custom'}\n"
            f"  Nodes stored: {len(merged)}\n"
            f"  Notes: {notes}"
        )

    def _optimize_heading(self, url: str, impl_data: dict) -> tuple[bool, str]:
        """Optimize H1/H2 headings on a page."""
        new_heading = impl_data.get("new_value", "")
        if not new_heading:
            return False, "No new heading value provided"

        post = self.wp.get_post_by_url(url)
        if not post:
            return False, f"Post not found: {url}"

        post_id = post.get("ID") or post.get("id")
        # Update the post title (which renders as H1 in most themes)
        ok = self.wp.update_post_title(int(post_id), new_heading)
        if ok:
            # Also update Rank Math title to match
            self.wp.update_rank_math_meta(int(post_id), title=f"{new_heading} | IndogenMed")
            self.wp.purge_cache()
            return True, f"Heading updated for post {post_id}: {new_heading}"
        return False, f"Failed to update heading for post {post_id}"

    def _create_content_brief(
        self, url: str, keyword: str, impl_data: dict
    ) -> tuple[bool, str]:
        """
        Create a WordPress draft post storing the content brief.

        Drafts are created under the 'post' post type with tag 'ci-content-brief'
        so editors can find them in WP Admin → Posts → Drafts.

        impl_data keys:
          notes       — detailed brief (outline, angles, word count recommendation)
          new_value   — primary action / goal
          current_value — current content summary or gap description
        """
        notes = impl_data.get("notes", "")
        goal = impl_data.get("new_value", f"Create optimized content targeting '{keyword}'")
        gap = impl_data.get("current_value", "")
        from datetime import date

        title = f"[Content Brief] {keyword}" if keyword else f"[Content Brief] {url}"

        content_lines = [
            f"<h2>Target Keyword</h2><p>{keyword}</p>",
            f"<h2>Target URL</h2><p>{url}</p>",
            f"<h2>Goal</h2><p>{goal}</p>",
        ]
        if gap:
            content_lines.append(f"<h2>Current Gap</h2><p>{gap}</p>")
        if notes:
            content_lines.append(f"<h2>Brief</h2><p>{notes}</p>")
        content_lines.append(f"<p><em>Generated by CI SEO Agent on {date.today().isoformat()}</em></p>")
        content_html = "\n".join(content_lines)

        # Create draft post via WP-CLI
        php = (
            f"$post_id = wp_insert_post(["
            f"  'post_title'   => {json.dumps(title)},"
            f"  'post_content' => {json.dumps(content_html)},"
            f"  'post_status'  => 'draft',"
            f"  'post_type'    => 'post',"
            f"  'meta_input'   => ['ci_content_brief' => '1', 'ci_brief_keyword' => {json.dumps(keyword)}, 'ci_brief_url' => {json.dumps(url)}]"
            f"], true); "
            # Ensure the tag exists and attach it
            f"$tag_id = term_exists('ci-content-brief', 'post_tag'); "
            f"if (!$tag_id) {{ $tag_id = wp_insert_term('ci-content-brief', 'post_tag'); }} "
            f"$tag_id = is_array($tag_id) ? (int)$tag_id['term_id'] : (int)$tag_id; "
            f"if ($tag_id) {{ wp_set_post_tags($post_id, [$tag_id], true); }} "
            f"echo is_wp_error($post_id) ? 'ERROR:' . $post_id->get_error_message() : 'OK:' . $post_id;"
        )

        stdout, stderr, rc = self.wp.wp_cli("eval", php, timeout=30)
        # LiteSpeed and other plugins may print to stdout before our echo — find our marker
        ok_line = next((l for l in stdout.splitlines() if l.startswith("OK:") or l.startswith("ERROR:")), "")
        if rc != 0 or not ok_line.startswith("OK:"):
            err = ok_line or stdout or stderr
            return False, f"Failed to create content brief draft: {err[:300]}"

        new_post_id = ok_line.split(":", 1)[1].strip()
        # Build site root from WP_BASE_URL (strip /wp-json/wp/v2 if present)
        site_root = re.sub(r"/wp-json.*$", "", cfg.WP_BASE_URL).rstrip("/")
        admin_url = f"{site_root}/wp-admin/post.php?post={new_post_id}&action=edit"
        logger.info(f"Content brief created as draft post {new_post_id} for keyword '{keyword}'")
        return True, (
            f"Content brief created as draft post #{new_post_id}\n"
            f"  Keyword: {keyword}\n"
            f"  URL: {url}\n"
            f"  Edit: {admin_url}\n"
            f"  Tag: ci-content-brief\n"
            f"  Brief: {notes[:200]}"
        )

    def execute_batch(
        self, actions: list[dict], max_actions: int = 10
    ) -> list[dict]:
        """
        Execute a batch of actions.
        Returns list of results.
        """
        results = []
        executed = 0

        for action in actions[:max_actions]:
            meta = action.get("metadata", action)
            action_id = meta.get("action_id", "unknown")

            success, message = self.execute_action(action)
            results.append(
                {
                    "action_id": action_id,
                    "action_type": meta.get("action_type", ""),
                    "target_url": meta.get("target_url", ""),
                    "success": success,
                    "message": message,
                }
            )
            executed += 1
            logger.info(
                f"Action {action_id} ({'OK' if success else 'FAIL'}): {message[:100]}"
            )

        return results


implementer = Implementer()
