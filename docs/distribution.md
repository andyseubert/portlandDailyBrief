# RSS, personal email and identity

Andy requested a subscribable output feed, personal daily email, logo concepts and domain research on October 28. This authorizes those requested outputs; public website deployment, mass newsletters, purchases and unreviewed civic narrative publication remain outside that request.

## Subscribe by RSS

Use [the daily RSS feed](https://raw.githubusercontent.com/andyseubert/portlandDailyBrief/main/feed.xml) in a feed reader. The XML is hosted through the existing GitHub summary archive, so a custom domain is not needed. Items link to the readable dated Markdown on GitHub and contain the validated brief's summary HTML. Private source descriptions, raw evidence, model inputs, review-queue files and email settings are excluded.

The archive job regenerates `feed.xml` with its normal five-minute sync. It validates the entire edition batch before replacing the feed. There is one stable item ID per dated edition; corrections update that edition rather than creating a duplicate day. RSS readers differ in how they display corrections. The configured feed retains thirty editions. Initial export contains all twenty existing October 9–28 editions; the next ordinary daily generation will add the permanent government section. Existing dated briefs were not regenerated with a text model for this change.

```sh
.venv/bin/python -m portland_brief.repository_archive
```

That command exports locally without committing/pushing. Use the existing authorized `--push` workflow only with a clean main branch, or commit explicit tested changes manually. `config/tasks.json` holds canonical feed/archive URLs; a future chosen domain can replace them after hosting is actually configured.

## Personal daily email

Email message creation, plain/HTML alternatives, authenticated TLS transport and a durable delivery ledger are implemented. **Real sending is disabled pending Andy's recipient address and authenticated sender setup. No real email was sent in this increment.** `output/daily-email-preview.eml` is a private placeholder preview addressed to `recipient@example.invalid`; it is not a delivered message.

Gmail was discovered as an available connection and suggested; suggestion does not mean connected. A conversational Gmail connection alone also does not give an unattended local LaunchAgent SMTP credentials. Do not claim scheduled email works until an actual sender is configured and an authorized test is observed. Secrets must never be pasted into chat, tracked files, logs or shell arguments.

Private settings go in ignored `data/email-delivery.json`, for example:

```json
{
  "to": "YOUR_ADDRESS",
  "from": "YOUR_AUTHENTICATED_SENDER",
  "smtp_host": "YOUR_PROVIDER_SMTP_HOST",
  "smtp_port": 587,
  "smtp_username": "YOUR_PROVIDER_USERNAME",
  "tls": "starttls",
  "keychain_service": "portland-daily-brief-smtp"
}
```

Store the provider's SMTP credential in the Mac's Keychain under that service, or use a named environment variable through `password_env` (default `PORTLAND_BRIEF_SMTP_PASSWORD`). Environment availability must be checked for the actual LaunchAgent, not only an interactive shell. Provider access requirements/permissions need verification at setup. No paid mail service has been provisioned.

```sh
.venv/bin/python -m portland_brief.delivery
.venv/bin/python -m portland_brief.delivery --send
```

The first command prepares a private `.eml` without transmitting. The second explicitly sends today's validated edition. Once configured and live-tested, set `email_delivery.enabled` true in `config/tasks.json`; the existing scheduler then checks every five minutes and sends once today's edition exists. It never substitutes yesterday's edition. A sent date/recipient is not resent on retries or ordinary corrections. Transmission uncertainty is held for reconciliation instead of risking duplicate mail; inspect private `data/delivery/email.sqlite3` and the actual sender before making an explicit retry decision. Missing credentials are rejected before transmission is claimed. This is personal delivery, not a mailing list.

## Logo and domain exploration

See [the identity preview](../assets/branding/identity-preview.html) and [original concept sheet](../assets/branding/logo-concepts-v1.png). The working name remains Portland Daily Brief pending Andy's preference. The center bridge/river monogram is the recommended direction; the other concepts emphasize a newspaper or a broader city guide. This is an original AI-generated concept sheet, not a final selected identity or a trademark clearance.

On October 28, direct requests to Verisign's authoritative `.com` RDAP endpoint returned HTTP 404/no registration record for these candidates:

| Candidate | Reason to consider |
| --- | --- |
| [portlanddailybrief.com](https://rdap.verisign.com/com/v1/domain/portlanddailybrief.com) | Matches the current project name; recommended default |
| [portlandcivicbrief.com](https://rdap.verisign.com/com/v1/domain/portlandcivicbrief.com) | Explicit government/accountability emphasis |
| [pdxbrief.com](https://rdap.verisign.com/com/v1/domain/pdxbrief.com) | Shorter local identity |
| [portlandcivicwatch.com](https://rdap.verisign.com/com/v1/domain/portlandcivicwatch.com) | Strong ongoing accountability focus |

These are dated registry observations, not guaranteed registrar availability, a price quote, a reservation or a purchase. Verify the chosen name at a registrar immediately before registration. The private check result is `output/domain-check-2026-10-28.json`. No public website or new hosting account was deployed.
