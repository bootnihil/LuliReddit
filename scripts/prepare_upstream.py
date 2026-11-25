#!/usr/bin/env python3
"""Apply a narrowly scoped, fail-closed Ilay r/all GraphQL and Old Reddit fallback patch.

Runs against a fresh upstream checkout, leaving the fork's upstream tree untouched.
Bump PATCH_REVISION when changing the shipped patch for the same upstream version.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

PATCH_REVISION = 2
APP_ID = "com.bennybar.luli_for_reddit.bootnihil"


def replace_exactly_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Refusing to patch {label}: expected one matching upstream block, found {count}. "
            "Upstream may have changed; review this patch before building."
        )
    return text.replace(old, new, 1)


def prepare(root: Path) -> tuple[str, int]:
    repo_path = root / "app/src/main/java/com/bennybar/luli_for_reddit/data/RedditRepository.kt"
    repo = repo_path.read_text(encoding="utf-8")

    # Important: this alters only the r/all empty-result path. Existing 403
    # handling, ordinary subreddits, r/popular, cached/offline pages and OAuth
    # behavior stay unchanged.
    old = '''        return orGql({
            val res = client.getResult("$base/${sort.path}", postsQuery(sort, time, after, limit))
            parse { parsePostListing(res.json, res.fromCache) }
        }) {
            // Reddit's app loads a feed page as post ids, then the posts by id
            // (its page sizes: 6 first, then 24).
            var cursor = after
            // PostsByIds drops removed posts, so a page can come back empty
            // while the feed goes on: skip ahead a few pages, not end it there.
            for (i in 0 until 5) {
                val (operation, hash, vars) = gqlFeedQuery(subreddit, sort, time, cursor) ?: throw RedditApiException(403)
                val ids = gqlFeedPostIds(client.gql(operation, hash, vars))
                val posts = getPostsByIds(ids)
                cursor = ids.lastOrNull()
                if (posts.isNotEmpty() || cursor == null) return@orGql Listing(posts, cursor)
            }
            Listing(emptyList(), null)
        }
'''
    new = '''        // Keep the existing GraphQL fallback for actual 403 responses, and
        // also use it when the website returns HTTP 200 with an empty r/all.
        // Reddit can retire a listing without returning an HTTP error.
        suspend fun graphQlFeed(): Listing<Post> {
            // Reddit's app loads a feed page as post ids, then the posts by id
            // (its page sizes: 6 first, then 24).
            var cursor = after
            // PostsByIds drops removed posts, so a page can come back empty
            // while the feed goes on: skip ahead a few pages, not end it there.
            for (i in 0 until 5) {
                val (operation, hash, vars) = gqlFeedQuery(subreddit, sort, time, cursor) ?: throw RedditApiException(403)
                val ids = gqlFeedPostIds(client.gql(operation, hash, vars))
                val posts = getPostsByIds(ids)
                cursor = ids.lastOrNull()
                if (posts.isNotEmpty() || cursor == null) return Listing(posts, cursor)
            }
            return Listing(emptyList(), null)
        }
        return orGql({
            val res = client.getResult("$base/${sort.path}", postsQuery(sort, time, after, limit))
            val listing = parse { parsePostListing(res.json, res.fromCache) }
            if (shouldRetryEmptyAllFeed(
                    subreddit = subreddit,
                    webMode = client.isWebMode(),
                    fromCache = res.fromCache,
                    empty = listing.items.isEmpty(),
                )) graphQlFeed() else listing
        }) { graphQlFeed() }
'''
    repo_path.write_text(replace_exactly_once(repo, old, new, "r/all listing fallback"), encoding="utf-8")

    # v2: intercept website-mode r/all BEFORE the retired modern JSON / GraphQL feed.
    repo = repo_path.read_text(encoding="utf-8")
    repo = replace_exactly_once(
        repo,
        '        val base = if (subreddit == null) "" else "/r/$subreddit"\n        // Keep the existing GraphQL fallback',
        '        if (subreddit.equals("all", ignoreCase = true) && client.isWebMode()) {\n'
        '            return fetchOldRedditAllPosts(sort, time, after, limit)\n'
        '        }\n'
        '        val base = if (subreddit == null) "" else "/r/$subreddit"\n'
        '        // Keep the existing GraphQL fallback',
        "Old Reddit r/all interception",
    )
    repo_path.write_text(repo, encoding="utf-8")

    predicate_path = repo_path.with_name("EmptyAllFeedFallback.kt")
    if predicate_path.exists():
        raise RuntimeError("Upstream now has EmptyAllFeedFallback.kt; review the patch before building.")
    predicate_path.write_text('''package com.bennybar.luli_for_reddit.data

/** Never replace r/all with r/popular. Only retry a live, empty r/all in web mode. */
internal fun shouldRetryEmptyAllFeed(
    subreddit: String?,
    webMode: Boolean,
    fromCache: Boolean,
    empty: Boolean,
): Boolean = subreddit.equals("all", ignoreCase = true) && webMode && !fromCache && empty
''', encoding="utf-8")

    tests_path = root / "app/src/test/java/com/bennybar/luli_for_reddit/data/EmptyAllFeedFallbackTest.kt"
    if tests_path.exists():
        raise RuntimeError("Upstream now has EmptyAllFeedFallbackTest.kt; review the patch before building.")
    tests_path.parent.mkdir(parents=True, exist_ok=True)
    tests_path.write_text('''package com.bennybar.luli_for_reddit.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EmptyAllFeedFallbackTest {
    @Test fun retriesOnlyLiveEmptyAllInWebsiteMode() {
        assertTrue(shouldRetryEmptyAllFeed("all", true, false, true))
        assertTrue(shouldRetryEmptyAllFeed("ALL", true, false, true))
        assertFalse(shouldRetryEmptyAllFeed("popular", true, false, true))
        assertFalse(shouldRetryEmptyAllFeed("android", true, false, true))
        assertFalse(shouldRetryEmptyAllFeed(null, true, false, true))
        assertFalse(shouldRetryEmptyAllFeed("all", false, false, true))
        assertFalse(shouldRetryEmptyAllFeed("all", true, true, true))
        assertFalse(shouldRetryEmptyAllFeed("all", true, false, false))
    }
}
''', encoding="utf-8")

    # Preserve the upstream changelog test with our BN version suffix.
    test_path = root / "app/src/test/java/com/bennybar/luli_for_reddit/feature/about/WhatsNewTest.kt"
    test_source = test_path.read_text(encoding="utf-8")
    test_source = replace_exactly_once(
        test_source,
        "assertEquals(com.bennybar.luli_for_reddit.BuildConfig.VERSION_NAME, CHANGELOG.first().version)",
        f'assertEquals(com.bennybar.luli_for_reddit.BuildConfig.VERSION_NAME.removeSuffix("-bn{PATCH_REVISION}"), CHANGELOG.first().version)',
        "WhatsNew version assertion",
    )
    test_path.write_text(test_source, encoding="utf-8")

    client_path = root / "app/src/main/java/com/bennybar/luli_for_reddit/core/net/RedditClient.kt"
    client = client_path.read_text(encoding="utf-8")
    client = replace_exactly_once(
        client,
        "    suspend fun get(path: String, query: Map<String, Any?> = emptyMap()): JsonElement = getResult(path, query).json",
        """    /** Old Reddit's real r/all HTML, with our existing website-session cookies. */
    suspend fun getOldRedditAllHtml(sort: String, time: String?, after: String?, limit: Int): String {
        ensureConfig()
        if (!webMode) throw IllegalStateException("Old Reddit feed requires website-session login.")
        val url = "https://old.reddit.com/r/all/$sort/".toHttpUrl().newBuilder()
            .addQueryParameter("limit", limit.coerceIn(1, 100).toString())
            .apply {
                if (time != null) addQueryParameter("t", time)
                if (after != null) addQueryParameter("after", after)
            }.build().toString()
        val (status, html) = execute("GET", url, null)
        if (status !in 200..299) throw RedditApiException(status)
        // A browser challenge, login page or unexpected HTML must not look like an empty feed.
        if (!html.contains("data-fullname") || !html.contains("siteTable")) {
            throw IllegalStateException("Old Reddit didn't return a recognizable r/all listing.")
        }
        return html
    }

    suspend fun get(path: String, query: Map<String, Any?> = emptyMap()): JsonElement = getResult(path, query).json""",
        "Old Reddit authenticated HTML fetch",
    )
    client_path.write_text(client, encoding="utf-8")

    old_reddit_path = repo_path.with_name("OldRedditAll.kt")
    if old_reddit_path.exists():
        raise RuntimeError("Upstream added OldRedditAll.kt; review before patching.")
    old_reddit_path.write_text('package com.bennybar.luli_for_reddit.data\n\nimport com.bennybar.luli_for_reddit.model.Listing\nimport com.bennybar.luli_for_reddit.model.Post\n\n// Old Reddit still offers the actual r/all feed (distinct from r/popular).\n// Read only post fullnames from its listing; hydrate post cards through Ilay\'s\n// existing PostsByIds implementation rather than interpreting HTML as posts.\nprivate val oldRedditTags = Regex("""<div\\b[^>]*>""", RegexOption.IGNORE_CASE)\nprivate val oldRedditClass = Regex("""\\bclass\\s*=\\s*(["\'])(.*?)\\1""", RegexOption.IGNORE_CASE)\nprivate val oldRedditFullname = Regex("""\\bdata-fullname\\s*=\\s*(["\'])(t3_[a-z0-9]+)\\1""", RegexOption.IGNORE_CASE)\nprivate val oldRedditNext = Regex("""\\bclass\\s*=\\s*(["\'])[^"\']*\\bnext-button\\b[^"\']*\\1""", RegexOption.IGNORE_CASE)\n\ninternal fun oldRedditAllPostIds(html: String): List<String> = oldRedditTags.findAll(html)\n    .mapNotNull { match ->\n        val tag = match.value\n        val classes = oldRedditClass.find(tag)?.groupValues?.get(2)\n            ?.split(Regex("""\\s+""")) ?: return@mapNotNull null\n        if ("thing" !in classes || "link" !in classes) return@mapNotNull null\n        oldRedditFullname.find(tag)?.groupValues?.get(2)\n    }\n    .distinct()\n    .toList()\n\ninternal fun oldRedditAllHasNext(html: String): Boolean = oldRedditNext.containsMatchIn(html)\n\n/** Website-session-only r/all fallback. Never substitutes the r/popular feed. */\ninternal suspend fun RedditRepository.fetchOldRedditAllPosts(\n    sort: PostSort,\n    time: TopTime,\n    after: String?,\n    limit: Int,\n): Listing<Post> {\n    // Old Reddit has no "best" sort for r/all; its canonical listing is "hot".\n    val legacySort = if (sort == PostSort.BEST) "hot" else sort.path\n    val html = client.getOldRedditAllHtml(\n        sort = legacySort,\n        time = if (sort.needsTime) time.param else null,\n        after = after,\n        limit = limit,\n    )\n    val ids = oldRedditAllPostIds(html)\n    if (ids.isEmpty()) {\n        throw IllegalStateException("Old Reddit r/all returned no post IDs (login, block, or changed HTML).")\n    }\n    val posts = getPostsByIds(ids)\n    if (posts.isEmpty()) {\n        throw IllegalStateException("Old Reddit listed ${ids.size} r/all posts, but Reddit\'s PostsByIds returned none.")\n    }\n    return Listing(posts, if (oldRedditAllHasNext(html)) ids.last() else null)\n}\n', encoding="utf-8")
    old_reddit_test_path = root / "app/src/test/java/com/bennybar/luli_for_reddit/data/OldRedditAllTest.kt"
    if old_reddit_test_path.exists():
        raise RuntimeError("Upstream added OldRedditAllTest.kt; review before patching.")
    old_reddit_test_path.parent.mkdir(parents=True, exist_ok=True)
    old_reddit_test_path.write_text('package com.bennybar.luli_for_reddit.data\n\nimport org.junit.Assert.assertEquals\nimport org.junit.Assert.assertFalse\nimport org.junit.Assert.assertTrue\nimport org.junit.Test\n\nclass OldRedditAllTest {\n    private val html = """\n        <div id="siteTable">\n          <div class="thing id-t3_a1 link odd" data-fullname="t3_a1"></div>\n          <div data-fullname=\'t3_b2\' class=\'link thing even\'></div>\n          <div class="thing comment" data-fullname="t1_c3"></div>\n          <div class="other" data-fullname="t3_d4"></div>\n          <div class="link thing odd" data-fullname="t3_a1"></div>\n        </div>\n        <span class="next-button"><a href="/r/all/?after=t3_b2">next</a></span>\n    """.trimIndent()\n\n    @Test fun parsesPostIdentifiersInOrderWithoutDuplicates() {\n        assertEquals(listOf("t3_a1", "t3_b2"), oldRedditAllPostIds(html))\n    }\n\n    @Test fun recognizesPaginationAndEndOfFeed() {\n        assertTrue(oldRedditAllHasNext(html))\n        assertFalse(oldRedditAllHasNext(html.substringBefore("<span class=\\"next-button\\">")))\n    }\n\n    @Test fun emptyOrBlockedPageDoesNotFabricatePosts() {\n        assertTrue(oldRedditAllPostIds("<html>Log in to Reddit</html>").isEmpty())\n    }\n}\n', encoding="utf-8")

    gradle_path = root / "app/build.gradle.kts"
    gradle = gradle_path.read_text(encoding="utf-8")
    m_code = re.search(r'^\s*versionCode\s*=\s*(\d+)\s*$', gradle, re.M)
    m_name = re.search(r'^\s*versionName\s*=\s*"([^"]+)"\s*$', gradle, re.M)
    if not m_code or not m_name:
        raise RuntimeError("Unable to parse upstream Android versionCode/versionName.")
    original_code = int(m_code.group(1))
    original_name = m_name.group(1)
    if PATCH_REVISION < 1 or PATCH_REVISION > 99:
        raise RuntimeError("PATCH_REVISION must be 1..99")
    final_code = original_code * 100 + PATCH_REVISION
    if final_code > 2_100_000_000:
        raise RuntimeError("Patched versionCode would overflow Android's supported range.")
    final_name = f"{original_name}-bn{PATCH_REVISION}"
    gradle = replace_exactly_once(
        gradle,
        'applicationId = "com.bennybar.luli_for_reddit"',
        f'applicationId = "{APP_ID}"',
        "application ID",
    )
    gradle = replace_exactly_once(gradle, m_code.group(0).strip(), f"versionCode = {final_code}", "versionCode")
    gradle = replace_exactly_once(gradle, m_name.group(0).strip(), f'versionName = "{final_name}"', "versionName")
    gradle_path.write_text(gradle, encoding="utf-8")

    constants_path = root / "app/src/main/java/com/bennybar/luli_for_reddit/core/RedditConstants.kt"
    constants = constants_path.read_text(encoding="utf-8")
    constants_path.write_text(replace_exactly_once(
        constants, 'const val GITHUB_REPO = "bennybar/LuliReddit"',
        'const val GITHUB_REPO = "bootnihil/LuliReddit"', "in-app updater GitHub repository"
    ), encoding="utf-8")

    strings_path = root / "app/src/main/res/values/strings.xml"
    strings = strings_path.read_text(encoding="utf-8")
    strings_path.write_text(replace_exactly_once(
        strings, '<string name="launcher_name">Ilay</string>',
        '<string name="launcher_name">Ilay BN</string>', "launcher label"
    ), encoding="utf-8")

    print(f"Prepared {final_name} ({final_code}), app ID {APP_ID}")
    return final_name, final_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path, help="Fresh upstream checkout")
    args = parser.parse_args()
    prepare(args.root)
