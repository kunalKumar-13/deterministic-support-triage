# Opting your website out of crawling by Anthropic

Anthropic's web crawler identifies itself by its user-agent. To opt your
website out of crawling, add a robots.txt rule that disallows the
crawler.

## robots.txt example

```
User-agent: anthropic-ai
Disallow: /

User-agent: ClaudeBot
Disallow: /
```

Use both user-agent names — Anthropic's crawler has used both
identifiers historically.

## What this does NOT do

- It does not remove content that was previously crawled. Submit a
  separate removal request through the Privacy contact form for past
  data.
- It does not prevent Claude users from manually pasting content from
  your site into a conversation.

## Verification

Anthropic publishes the IP ranges of its crawler. You can verify
incoming requests against these ranges if you want stronger evidence
than the user-agent string.
