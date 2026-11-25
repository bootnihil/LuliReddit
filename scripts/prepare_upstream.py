#!/usr/bin/env python3
"""Apply a narrowly scoped, fail-closed Ilay r/all GraphQL fallback patch.

Runs against a fresh upstream checkout, leaving the fork's upstream tree untouched.
Bump PATCH_REVISION when changing the shipped patch for the same upstream version.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

PATCH_REVISION = 1
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
