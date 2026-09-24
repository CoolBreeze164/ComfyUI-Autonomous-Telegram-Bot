# ComfyUI Autonomous Telegram Bot ◀️

Create a fully autonomous Telegram bot inside a single ComfyUI workflow without any 3rd party software and complicated server setups. Simply copy your bot_token, hit Run and ***that's it***! Share your bot with friends on Telegram so they can generate something using ComfyUI, running on your computer!

The pack features a unified user message receiver (uses Long Polling) and a rich collection of media senders. The nodes are designed for continuous operation of the workflow under the **"Run (Instant)"** queueing mode. Each received message is processed with its own chat ID. The pack effectively allows two-way communication between Telegram and ComfyUI.

Simple example bot workflows can be found in `examples/`. At the end of this *README* you can find a Civitai link to an advanced bot workflow that I made using a variety of other custom node packs!

---

## 🧩 List of Nodes

#### The pack is largely based on [ComfyUI Telegram Bot Node](https://github.com/AKharytonchyk/ComfyUI-telegram-bot-node) and [ComfyUI Telegram Suite](https://github.com/SwissCore92/comfyui-telegram-suite).

## ◀️ Unified message receiver

**Telegram Listener** node adapted from [ComfyUI Telegram Bot Node](https://github.com/AKharytonchyk/ComfyUI-telegram-bot-node) and improved with additional media outputs, optimized robustness and latency, as well as new `access_mode`. The original code was redesigned to work under **Run (Instant)** mode specifically. The node has four widgets:

| Widget | Value |
| --- | --- |
| `bot_token` | Your bot's unique token from @BotFather |
| `timeout_time` | Queue waiting window per run, max 300 seconds, default 10; returns immediately when a message is ready |
| `access_mode` | `blacklist` (default) or `whitelist` |
| `chat_ids` | Multiline numeric ChatIDs to blacklist/whitelist, separated by newlines, spaces, commas or semicolons |

**Blacklist** rejects listed ChatIDs; an empty list allows everyone. **Whitelist** accepts only listed ChatIDs; an empty list allows nobody. Keep the minus sign on group ChatIDs. These are **chat** IDs: a group ID affects the whole group, not individual members. Use private chats if filtering individual users. Invalid entries produce a configuration error before consuming any message.

The current policy is checked on every dequeued message, including buffered messages and download retries, before downloading or decoding media. A rejected message logs its ChatID and is discarded permanently. All eight outputs become silent blockers for that run, so dependent branches and senders are skipped. The run succeeds, letting **Run (Instant)** safely submit the next one. Connect downstream nodes to the receiver; unconnected output branches are outside this filter.

Output returns the message sent by the user to the bot. Features outlets for text message and various media, as well as message information.

| Output | ComfyUI type | Value |
| --- | --- | --- |
| `message_text` | STRING | Message text, command, or media caption; empty when absent |
| `chat_id` | INT | The incoming message's exact chat ID, including negative group IDs |
| `message_image` | IMAGE | RGB float32 tensor `[1, height, width, 3]` |
| `message_video` | VHS_FILENAMES | `(False, [local_video_path])`; can connect directly to Send Video |
| `message_audio` | AUDIO | Decoded waveform `[1, 2, samples]`, sample rate 48000 Hz |
| `message_id` | INT | The ID of the incoming message |
| `message_thread_id` | INT | Forum topic/thread ID, or -1 if absent |
| `message_type` | STRING | `text`, `image`, `video`, or `audio` |

Connect `chat_id` to all the senders in the workflow. If the bot is meant to reply in a forum topic, connect `message_thread_id` too. In a group, replies go to that group; in a private conversation, to that user.

**<details><summary>⚙️ Technical stuff**
</summary>

Photos use the largest available Telegram size. Images sent as documents are also accepted; EXIF orientation is applied and images are converted to RGB. Video/animation and audio/voice messages are supported, as are documents with image, video or audio MIME types. Unsupported service messages and update types are skipped. An incoming Telegram album is handled as separate messages, one item per workflow run. Edited incoming messages are not treated as new jobs.

An absent media output is a **silent ExecutionBlocker**, so its dependent branch does not run. For example, a text-only message cannot produce a fake image for an image generation branch. Always connect the appropriate receiver output to the branch that should be conditional.

On an idle timeout, **all** outputs are silent blockers. Dependent processing and senders are skipped, the run finishes successfully, and **Run (Instant)** submits the next run. Independent output branches with no receiver dependency can still execute, as in any ComfyUI workflow. `timeout_time` controls waiting for the inbox, not a polling frequency or a delay imposed on available messages. An allowed attachment is then downloaded/decoded using separate network timeouts.

### How continuous operation works

The receiver's polling loop and ComfyUI's built-in **Run (Instant)** queue work together:

1. `IS_CHANGED` returns a fresh `float("nan")` on every execution check. This invalidates the receiver and its dependent nodes even when tokens, timeouts, text and chat IDs repeat. Expensive independent model loaders can stay cached.
2. One daemon thread per token long-polls Telegram and queues complete update records while ComfyUI works. The receiver consumes one ready message or waits on a condition variable, yielding a silent result on timeout or rejection. The 10-second server long-poll timeout returns early when a message arrives; it is not a 10-second delay added to messages.
3. The workflow completes. ComfyUI's **Run (Instant)** scheduler submits the next run when the queue becomes empty. The receiver is also an output node, so it executes even when used on its own.

ComfyUI remains the single queue owner. No additional requeue callback is registered, which keeps batch count 1 from becoming two competing queues. Ordinary Run consumes/waits for one message. Instant repeats until stopped. No manual seed changes, extra loop node, or disable-all-caching launch flag is needed.

Transient polling/network failures retry for **at most 90 seconds per consecutive outage**, counting request time and retry delays from the start of the first failed request. Delays begin at 0.25, 0.5, 1 and 2 seconds, then 5 seconds. A successful poll resets the outage timer; ordinary idle workflow reruns do not. Request timeouts shrink to fit the remaining budget. Telegram's `retry_after` is respected: if it exceeds the remaining budget, the listener stops when that budget expires instead of retrying early.
At the limit, the background thread exits and logs one final stop message. The receiver surfaces the stop on its next execution, stopping Run (Instant) instead of silently starting a new retry cycle. Restore connectivity and press **Run** again to restart polling without restarting ComfyUI. ⚠️ **If the workflow was paused when the connection error occurred, the first Run reports the stored error; the following Run restarts normally.** The existing cursor and buffered messages are retained. The limit can be adjusted in `nodes/receiver.py` (parameter `RECEIVER_RETRY_TIMEOUT_SECONDS`).

The inbox applies backpressure at 1,000 queued records (plus in-flight work), leaving additional updates on Telegram until there is space. A transient attachment-download failure produces no outputs and keeps the message for a later run, with backoff. Other ready messages can proceed. After five failed download attempts, or a permanent file-download rejection, the attachment's message is skipped with a console warning. No partial text or chat ID is emitted for that failed message. Cancellation during downloading retains the message for when processing resumes. Downloading/decoding media still takes time; the background poller continues meanwhile.

Invalid bot credentials and polling conflicts surface an actionable error. After fixing a conflict, **Run** can restart the poller. Outgoing network failures are retried and handled without an execution error, as described below.
</details>

## ◀️ Senders and editors

The original [ComfyUI Telegram Suite](https://github.com/SwissCore92/comfyui-telegram-suite) sender/editor **inputs, outputs and widgets** are retained, with the nodes now directly using **bot_token STRING widget**. The code was additionally reviewed and optimized for minimal response latency, improved compatibility and operation under **Run (Instant)** mode. Unnecessary nodes were removed from the original pack.

| Node | Functionality |
| --- | --- |
| Send Message | Text, parse mode, notifications, protected content, topic ID |
| Send Image(s) | PNG/JPG/WEBP; individual images or groups up to 10; photo or file |
| Send Video | VHS_FILENAMES input; video, animation or file |
| Send Audio | AUDIO input; voice/Opus, audio/MP3 or WAV file |
| Send Chat Action | Typing, uploading, recording and the other chat actions |
| Edit Message Text | Replace text of a previously sent bot message |
| Edit Message Caption | Replace or clear a media caption |
| Edit Message Image | Replace photo/document media with the first image in the input batch |
| Edit Message Video | Replace video/animation/document media |
| Edit Message Audio | Replace audio/document media |
| API Method | Call another Telegram Bot API method with a DICT of parameters (kept from original pack) |
| Parse JSON | Convert a JSON string to DICT for API Method (utility kept from original pack) |

Senders and editors retain their `message`, `message_id`, and `trigger` outputs. For multiple sent images, `message` and `message_id` refer to the **last** image. Send Chat Action retains the original `BOOL` and wildcard outputs, and its required `message_thread_id` socket. Connect receiver `message_thread_id`, which returns -1 for ordinary chats. `trigger` connections enforce execution order.

For an edit node, wire `message_id` output **from a sender**, not the receiver's incoming message ID: the bot normally edits messages it sent. Wire the same chat ID to the sender and editor. Queue workflow actions in the order you want by using `trigger` passthrough on senders and editors.

**<details><summary>⚙️ Technical stuff**
</summary>

### Sender network retries

All senders, editors, Send Chat Action and API Method use the same retry policy:

| Setting | Default |
| --- | --- |
| Total attempts per API request | 5: first request plus 4 retries |
| Connection timeout | 3, 5, 10, 10, 10 seconds across attempts |
| Text/action/API read timeout | 10, 15, 30, 30, 30 seconds across attempts |
| Media read / upload write timeout | 120 seconds per attempt |
| Connection-pool timeout | 3 seconds |
| Delays before retries | 0.25, 0.5, 1, then 2 seconds |
| Telegram rate limiting | Wait at least the API's `retry_after` value |

Connection failures, timeouts, other HTTP transport failures, Telegram 429 rate limits, HTTP 408 and 5xx service errors are retryable. Damaged/incomplete API responses are also retryable. Media methods retain the longer read allowance even when referencing a Telegram file ID or URL instead of uploading bytes. Successful requests have no artificial delay. Per-token HTTPX clients reuse connections between nodes and runs, with separate pools for sends, polling and downloads. Each pool allows eight connections and 60-second keepalive expiry. You can adjust the sender time constants at the top of `nodes/sender_retry.py` and restart ComfyUI.

If all five attempts fail, the node logs a console warning and returns normally. This does **not** generate ComfyUI's `execution_error` or stop **Run (Instant)**. The current unsuccessful request is skipped; it is not placed in a background outbox for later delivery. The next workflow run can handle the next message.

- `message` and `message_id` become silent ExecutionBlockers, preventing a later edit node from acting on a fabricated or stale message ID.
- The `trigger` value passes through unchanged, allowing dependent processing to continue where it only needs the trigger.
- Send Chat Action returns `False` on failure and preserves its trigger.
- API Method returns a silent blocker for its result.

Retries apply to the **current HTTP request**, so an individual image batch does not resend its earlier successful images. If a response is lost after Telegram has accepted a request, retrying that request can still produce a duplicate message. This delivery ambiguity also applies to the original suite's retry approach.

Cancellation remains available during retry delays and between attempts. An in-flight HTTP request must first finish or reach its timeout. Permanent API errors such as invalid credentials, forbidden chats or invalid parameters still surface as errors; they are not treated as an internet outage.

Telegram advises avoiding more than one message per second **in a single chat** and caps group sends at 20 per minute. A real `retry_after` can therefore still cause a 20-second wait. The pack honors it; connection reuse cannot remove a server-imposed delay. See [Telegram Bots FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

</details>

## 🛠️ Installation

### Method 1: Via ComfyUI Manager (Recommended)
1. Open ComfyUI and click on the **Manager** button.
2. Click **Custom Nodes Manager**.
3. Search for `ComfyUI Autonomous Telegram Bot` and click **Install**.
4. Restart ComfyUI.

### Method 2: Manual Installation

1. Extract the ZIP. Place the **СomfyUI-Autonomous-Telegram-Bot** folder directly in
   `ComfyUI/custom_nodes/`. Or clone this repo:
   ```bash
   git clone https://github.com/CoolBreeze164/ComfyUI-Autonomous-Telegram-Bot
   ```
2. Install requirements with the Python environment that runs ComfyUI.

   Normal installation, from the ComfyUI directory:

   ```bash
   python -m pip install -r custom_nodes/ComfyUI-Autonomous-Telegram-Bot/requirements.txt
   ```

   Windows portable version installation, from its top-level directory:

   ```bash
   python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\ComfyUI-Autonomous-Telegram-Bot\requirements.txt
   ```

3. Restart ComfyUI. Find the nodes under **"Autonomous Telegram Bot ◀️"**.


The runtime requirements are **httpx, numpy, Pillow, and PyAV** (`av>=14.2.0`). Most of them (as well as Torch) come with ComfyUI. Use the requirements command above with ComfyUI's own Python, including `python_embeded\python.exe` for Portable. No separate Telegram Python SDK, web server, or configuration file is required. A lightweight background thread collects messages during operation.

Use a current ComfyUI version with `ExecutionBlocker` support and a frontend offering Run (Instant). Python 3.12+ is recommended with a supported ComfyUI build.

## ⚡️ How to set up a bot

To do a simple test of the bot follow these steps:

1. Create your new Telegram bot using @BotFather and get your bot's unique HTTP API token (just google it if you are not sure how to do that). The token should look like this: `0123456789:AbCdEfGhIjKlMnOpQrStUvWxYz0AbCdEfGh`
 2. Load `examples/example_text_echo.json` into ComfyUI.
3. Enter your bot's token in the purple text node, which is connected to receiver's and sender's `bot_token` widgets. The `chat_id` and the `message_text` are already wired from receiver to sender.
4. Select **Run (Instant)**, set the batch count to **1**, and click Run once.
5. Open the bot in Telegram and send a message. As long as the workflow tab is opened and running, the bot should automatically reply to you with your own text message.

If you have Krea-2 image generator downloaded, you can try the other example wokrflow `example_t2i_krea2.json`. It receives user's text message as a prompt and replies with an image based on that prompt.

⚠️ **Important note.** Because the bot uses ComfyUI for simple HTTP calls, it does not connect to your installed Telegram app in any way and thus, cannot utilize its built-in protocols like MTProto or SOCKS for connection with Telegram servers. This means that if Telegram is **blocked in your country**, the bot will not be able to call the server. To bypass restrictions, I recommend using [Cloudflare's WARP](https://one.one.one.one/) (Traffic and DNS (UDP) or Traffic and DNS (HTTPS) modes) or a VPN.

⚠️ **VERY IMPORTANT NOTE.** If you want to build your own bot workflow remember this:
Widget tokens are saved in workflow JSON. Remove them before sharing a workflow with someone else (or add a file reader that outputs the token value from a text file).
Image and audio senders encode the supplied pixels/samples **WITHOUT** adding workflow metadata JSON.
**HOWEVER**, video and animation senders/editors upload the original file bytes and filename unchanged, **retaining the existing metadata in those files**. To avoid this problem, use VHS Suite "Video Combine" with `save_metadata` mode **disabled** and connect its `Filenames` output directly to the video sender.

**<details><summary>⚙️ Some extra operational details**
</summary>

Each run handles at most one supported Telegram message. Further messages are collected in the background and normally run in the order they were received, including identical messages and messages from different users. A failed media download is deferred for retry, allowing another user's ready message to proceed. Use one receiver node and one active workflow per bot token. Keep ComfyUI and its browser tab open with this workflow active: **Run (Instant)** requeueing is controlled by the ComfyUI frontend. Closing the tab stops future automatic submissions.

To stop, disable **Run (Instant)** with its Stop control. Use ComfyUI's cancel/interrupt control as well if you want to interrupt the current run. The receiver checks for interruption at most every 0.1 seconds while waiting for a queued message, independently of any ongoing poll. A foreground media download or conversion can take longer to return. Background collection continues while a workflow is paused, up to the inbox capacity. When connectivity fails, each poller stops after the 90-second outage budget, so warnings do not continue indefinitely. Pausing **Run (Instant)** alone stops workflow processing and replies; healthy background collection continues up to the inbox capacity. Restarting ComfyUI is not needed to recover from an exhausted retry budget; see the Run behavior in technical details above.

A shared in-process inbox prevents competing `getUpdates` calls inside this pack, but cannot coordinate another ComfyUI process. Telegram polling and webhooks are mutually exclusive. If needed, call `deleteWebhook` with `drop_pending_updates=false` through API Method before starting the receiver. The receiver never deletes a webhook or discards Telegram's pending updates automatically.

The inbox/cursor are in memory and survive consecutive runs and node-instance recreation within the same ComfyUI process. They are not a persistent job database. A process crash, restart, or failed downstream operation can lose a fetched-but-unprocessed item or replay an unacknowledged item. There is no exactly-once delivery guarantee across restarts.

Received videos are written under ComfyUI's temporary directory in `ComfyUI/temp`. They remain available to downstream nodes and follow your ComfyUI temporary-file cleanup policy.

Public Telegram file limits apply: downloads through `getFile` are currently limited to 20 MB. Unavailable/oversized files are logged and skipped, as described above. Local media decoding/conversion errors still need correction. See the [Telegram Bot API](https://core.telegram.org/bots/api#file).

</details>

## 🤖 My prebuilt multifunctional bot workflow
If you are too lazy to build your own bot workflow, check out the one I made! It's a fully fledged and optimized **All-in-One** bot workflow/framework with multiple features and functionalities that include:

- **Smart message handler**: the bot accepts different commands that change it's state, enabling turn-based operation.
- **Different generators**: text2image (Krea-2), image edit (Flux2 Klein), LLM chat with TTS voiceover (Omnivoice), text2video, image2video and reference2video (Minimax H3). For the purpose of using image-requiring functions, the bot can accept an uploaded image or automatically use previously generated one.
- **Independent persistent cache of chat data for every user**: saved image (for image-requiring functions), generation parameters set by the user (aspect ratio, video duration and workflow state are saved in special config files) and LLM chat history. All of this data is saved independently for different users that interact with the bot (based on their ChatID), thus, excluding any possibility of potential data sharing between them.
- **Multiple users can interact with the bot at the same time** without any errors or bugs. Though the bot cannot answer while generating something, so the messages are queued.

Of course, you can expand upon those features if you figure out the workflow structure. I uploaded this mega-workflow and the bot demo to Civitai. Here is the link: ***(WORKFLOW LINK COMING SOON!)***
Note that the workflow uses many different custom node packs. Most of them are widely used (like KJNodes or comfyui-easy-use) and have low requirements.

The workflow also features [my other custom node pack](https://github.com/CoolBreeze164/ComfyUI-CoolB-Nodes) that I uploaded separately. It has some simple utility nodes that I designed specifically to create this bot workflow. Highly recommend it if you want to build your own advanced bot.

## 👥 Credits & Acknowledgements

This node pack was vibecoded using GPT-6 Astra. Code is based on the MIT-licensed [ComfyUI Telegram Bot Node](https://github.com/AKharytonchyk/ComfyUI-telegram-bot-node) and [ComfyUI Telegram Suite](https://github.com/SwissCore92/comfyui-telegram-suite). If you like this improved repack, go give each of those two original packs a star! ⭐️⭐️
