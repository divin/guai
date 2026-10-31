+++
title = "GUAI"

[extra]
tagline = "Your health, understood. A private, self-hosted health tracker built around your Pebble."
image = "images/watchface-dark.png"
image_alt = "GUAI health dashboard on the Pebble watchface"
cta_text = "Get GUAI"
cta_url = "https://github.com/divin/guai/releases/latest"
secondary_text = "Explore the project"
secondary_url = "https://github.com/divin/guai"

[[extra.features]]
icon = "⌚"
title = "Health at a glance"
description = "Track steps, sleep, and heart rate with a Pebble watchface that syncs to your own server."

[[extra.features]]
icon = "🛟"
title = "Lost-watch alert"
description = "Show a personal contact message after a configurable Bluetooth disconnect timeout."

[[extra.features]]
icon = "✅"
title = "Wrist-based check-ins"
description = "Create scheduled questions and answer boolean, scale, or choice prompts directly on your watch."

[[extra.features]]
icon = "📊"
title = "Understand your data"
description = "Explore health summaries, trends, weather, and check-in history in the self-hosted dashboard."

[[extra.features]]
icon = "🔒"
title = "Your data stays yours"
description = "Keep health and check-in records in your own SQLite database, on infrastructure you control."

[[extra.screenshots]]
image = "images/dashboard-overview.jpg"
alt = "GUAI dashboard overview with health summaries and charts"
caption = "Health overview"

[[extra.screenshots]]
image = "images/dashboard-check-ins.jpg"
alt = "GUAI dashboard check-ins page"
caption = "Manage scheduled check-ins"

[[extra.screenshots]]
image = "images/dashboard-trends.jpg"
alt = "GUAI dashboard trends table"
caption = "Review daily trends"

[[extra.screenshots]]
image = "images/check-in-emery.png"
alt = "GUAI Check-In running on a Pebble watch"
caption = "Answer check-ins on your Pebble"
+++

## One connected view of your health

GUAI brings passive health tracking and personal check-ins together. The Pebble watchface collects activity, sleep, and heart-rate data; the companion app lets you answer your own questions from your wrist; and a lightweight dashboard helps you explore patterns over time.

## How it works

1. **Wear your Pebble.** The watchface reads available Health data and sends snapshots through the phone to your server. Uploads received by the phone are queued locally when the server or network is unavailable.
2. **Check in from your wrist.** Create daily, weekly, or one-time questions, then answer them on the watch with a simple choice, scale, or yes/no response.
3. **Explore on your dashboard.** View activity, sleep, heart rate, weather, and check-in history together. Your records stay in your server's SQLite database.

## Made for everyday use

The watchface is customizable: choose what appears in its stats panel, set a step goal, select a light or dark theme, and adjust the upload interval. If the watch loses its phone connection, an optional lost-watch alert can replace the watchface with a custom contact message after your chosen delay. The message disappears when the watch reconnects.

GUAI is open source and designed for a self-hosted setup. Start with the [quick-start guide](https://github.com/divin/guai#-quick-start), or browse the [source code and documentation](https://github.com/divin/guai).
