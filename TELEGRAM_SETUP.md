# Telegram setup and operation

The updated dashboard on this Mac is [http://127.0.0.1:8766/#settings](http://127.0.0.1:8766/#settings). This address works on this computer only; it is not a phone-accessible website.

1. Create a dedicated bot using `/newbot` in Telegram's verified [@BotFather](https://t.me/BotFather). This step is already done for this setup.
2. On the computer, open Settings → **Connect your Telegram**. Paste the BotFather token into the password field and click **Create phone linking link**. Do not paste it into a chat, a screenshot or a Git repository.
3. Open the generated `t.me` link using Telegram and tap Start. If Telegram is only on your phone, open your bot there and send the exact `/start …` command displayed on the computer. The plain bot homepage link alone does not include the pairing code.
4. Wait for **Your Telegram account is linked** and the **Mentalist linked** welcome message. Pairing links expire in ten minutes; generate another if necessary.
5. Send `/status` to confirm that commands work. Use **Send a test alert** in Settings to verify the notification path.
6. To send alerts to a group, add the bot to the group and, from the same Telegram account you paired, send `/setgroup@your_bot_username` in that group. The dashboard will show the group as the alert destination. To switch alerts back to your private chat, send `/privatealerts` to the bot in your DM.

The pairing mechanism uses Telegram's [documented deep links](https://core.telegram.org/bots/features#deep-linking) and [Bot API long polling](https://core.telegram.org/bots/api#getupdates). It does not require a public webhook or opening your computer to incoming internet requests.

## What the bot can do

| Command | Response |
|---|---|
| `/help` | Available commands |
| `/status` | Monitor state, source check times and pending deliveries |
| `/portfolio` | Holdings and average acquisition cost from your ledger |
| `/risk` | Recorded-equity weights, data gaps and stock-limit warnings |
| `/quality` | Measured headline alert counts and delivery latency |
| `/feedback ID useful` | Rate a headline; also accepts noise, incorrect or duplicate |
| `/stock KPITTECH` | Saved price/date, philosophy scores, current model label and explanation |
| `/news TATAPOWER` | Recently stored headlines with links |

Only the paired private Telegram user/chat can request this information. A group receives automatic alerts only after the paired user links it with `/setgroup`; command replies and private portfolio information remain in the paired DM. Other group members cannot link a group or request commands. Forwarded stories and third-party channels are not ingested as verified exchange announcements. Commands do not place orders or change the portfolio. `/feedback` saves your assessment locally; it does not retune investment rules.

## What arrives automatically

- Fresh Google News headlines for holdings and shortlisted companies.
- Official NSE corporate announcements when the issuer matches a tracked company name or explicit alias.
- Model-status changes after local prices, financials, settings or freshness change. Initial observations establish a baseline without resending every existing label.
- Daily confirmations of an abnormal drop or subsequent price stabilisation.

Google checks run roughly every 60 seconds for a small watchlist, plus request time. More than 20 names rotate through rounds. NSE checks run every five minutes in accordance with its RSS TTL. Processing and Telegram delivery add latency, as do source publication/indexing delays. This is not a guaranteed real-time market-data service.

Alerts include source links and timestamps. Keyword labels such as “urgent review” are triage, not a verified finding of fraud/default or a trading decision. The app stores older headlines but only pushes ones no more than 60 minutes old; an extended outage can therefore leave historical news unpushed.

## Keeping it running

The macOS installer adds two per-user LaunchAgents:

- `org.mentalist.screener`: local dashboard on port 8766 plus the continuous monitor; restarts at login and after failure.
- `org.mentalist.daily`: weekday daily analysis at 18:30 in the Mac's system timezone. This Mac is configured to IST. If its timezone changes, the schedule follows the Mac.

Closing a browser tab does not stop the service. Logging out, sleep, shutdown and internet outages interrupt monitoring. No sleep settings have been changed. A dedicated always-on host and appropriate data feed are needed for a stronger availability guarantee.

```sh
python3 -m india_screener service show
python3 -m india_screener service remove
python3 -m india_screener service install --port 8766
```

The project folder and Python installation must remain at their configured locations. Reinstall the services after moving either. On Windows, `Start Screener.bat` starts the dashboard and monitor; closing that Python process stops monitoring. The existing scheduled daily task is separate.

## Troubleshooting and privacy

- **Waiting for Start:** use the generated pairing link/code, not just the bot's homepage; use a private chat; regenerate after ten minutes.
- **Group alerts not arriving:** send `/setgroup@your_bot_username` in the group from the paired account, then check that the dashboard names the group as the alert destination. If the bot uses privacy mode, address the command to the bot with `@your_bot_username`.
- **HTTP 401 / token rejected:** paste the full current BotFather token without spaces. If exposed, revoke it with BotFather and pair again.
- **Webhook already configured:** create a dedicated bot. The app does not remove another application's webhook.
- **HTTP 409:** another program is likely polling this bot. Stop the other bot consumer. This project prevents duplicate monitors for its own data directory.
- **Linked, no message:** check pending deliveries, whether the bot is blocked in Telegram, and the computer's internet connection. Telegram rate limits and connection failures are retried with backoff.
- **No news:** imported companies are not automatically subscribed. Add names to the shortlist or record holdings. No fresh news can also be a legitimate result.
- **Disconnect:** use the local Disconnect button. It disables local and legacy Telegram delivery and cancels pending Telegram sends. A message already in flight may still arrive. Re-pair to reconnect.

Credentials are saved only in `.local/telegram.json`, with owner-only permissions on macOS. API status and logs do not reveal the token. Your portfolio command responses go to your paired Telegram chat and the local alert log. This uses ordinary Telegram bot chats, not Secret Chats. Keep the `.local` directory and financial database out of public backups/repositories.

Delivery is persistent and at least once: event changes and queue insertion commit together, and retries survive restarts. If Telegram accepts a send but the connection fails before acknowledgement, a retry may duplicate it. “Pending” includes scheduled retries; it does not mean the message has reached the phone.
