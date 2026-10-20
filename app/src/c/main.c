#include <pebble.h>

// GUAI Check-In app (spec/integration.md). pkjs drives questions over AppMessage;
// C renders one question at a time and returns the answer; pkjs submits it.
//
// Flow: Loading -> Question (Select/Confirm) -> Sending -> Feedback -> next question | All done.
// Single BACK during a question cancels it (it stays pending server-side, retried later);
// BACK on the confirm screen returns to the question; long-BACK exits the app.

#define MAX_QUESTION_LEN 256
#define MAX_OPTION_LEN 80
#define MAX_OPTIONS 16

typedef enum {
  STATE_LOADING,
  STATE_SELECT,
  STATE_CONFIRM,
  STATE_SENDING,
  STATE_FEEDBACK,
  STATE_IDLE,       // no question to show: unconfigured / no pending / skipped
} AppState;

static Window *s_window;
static Layer *s_chrome_layer;        // canvas: button rail, glyphs, divider, counter bar
static TextLayer *s_wordmark_layer;
static TextLayer *s_clock_layer;     // "HH:MM" 24h clock, top right
static TextLayer *s_counter_layer;   // "N OF M" question counter
static TextLayer *s_anslabel_layer;  // "YOUR ANSWER" label on the confirm screen
static TextLayer *s_title_layer;
static TextLayer *s_value_layer;
static TextLayer *s_footer1_layer;   // footer hint 1 (e.g. "UP / DOWN CHOOSE")
static TextLayer *s_footer2_layer;   // footer hint 2 (e.g. "SELECT TO SEND")

// Theme tokens — same palette as the watchface (see apply_colors below and the
// watchface's apply_colors in watchface/src/c/guai.c).
static int s_theme = 0;            // 0 = dark (default), 1 = light
static GColor s_col_bg;
static GColor s_col_fg;            // primary text
static GColor s_col_muted;         // secondary text (hints, unit labels)
static GColor s_col_panel;         // panels / button rail background
static GColor s_col_accent;        // teal accent (wordmark, fills, bars)
static GColor s_col_on_accent;     // text drawn on an accent fill (contrast-safe)

static AppState s_state = STATE_LOADING;
static bool s_busy = false;          // reject new questions while mid-question
static char s_instance_id[40];
static char s_question[MAX_QUESTION_LEN];
static char s_answer_type[16];
static char s_options[MAX_OPTIONS][MAX_OPTION_LEN];
static int s_option_count = 0;
static int s_scale_min = 0;
static int s_scale_max = 10;
static int s_selection = 0;          // boolean 0/1, scale value, or option index
static char s_last_status[40];
static char s_idle_hint[48];         // hint line for the STATE_IDLE screen
static int s_q_index = 0;            // question counter (from pkjs: QUESTION_INDEX/COUNT)
static int s_q_count = 0;
static char s_clock_buf[8];

// ---------------------------------------------------------------------------
// Text helpers

static void set_text(TextLayer *layer, const char *text) {
  text_layer_set_text(layer, text);
  layer_mark_dirty(text_layer_get_layer(layer));
}

// Watchface palette (mirrors watchface/src/c/guai.c apply_colors):
// dark: bg black, fg white, muted 9AA3AD, accent 36B79F, panel navy 141C33;
// light: bg white, fg 1B1F27, muted 5A6170, accent 1E9C82, panel F0F1F4.
static void apply_colors(int theme) {
  if (theme == 1) {
    s_col_bg = GColorWhite;
    s_col_fg = GColorFromHEX(0x1B1F27);
    s_col_muted = GColorFromHEX(0x5A6170);
    s_col_panel = GColorFromHEX(0xF0F1F4);
    s_col_accent = GColorFromHEX(0x1E9C82);
    s_col_on_accent = GColorWhite;
  } else {
    s_col_bg = GColorBlack;
    s_col_fg = GColorWhite;
    s_col_muted = GColorFromHEX(0x9AA3AD);
    s_col_panel = GColorFromHEX(0x141C33);
    s_col_accent = GColorFromHEX(0x36B79F);
    s_col_on_accent = GColorFromHEX(0x1B1F27);
  }
  s_theme = theme;
  if (s_window) window_set_background_color(s_window, s_col_bg);
  if (s_chrome_layer) layer_mark_dirty(s_chrome_layer);
  if (s_wordmark_layer) text_layer_set_text_color(s_wordmark_layer, s_col_accent);
  if (s_clock_layer) text_layer_set_text_color(s_clock_layer, s_col_fg);
  if (s_counter_layer) text_layer_set_text_color(s_counter_layer, s_col_accent);
  if (s_anslabel_layer) text_layer_set_text_color(s_anslabel_layer, s_col_muted);
  if (s_title_layer) text_layer_set_text_color(s_title_layer, s_col_fg);
  if (s_value_layer) text_layer_set_text_color(s_value_layer, s_col_fg);
  if (s_footer1_layer) text_layer_set_text_color(s_footer1_layer, s_col_muted);
  if (s_footer2_layer) text_layer_set_text_color(s_footer2_layer, s_col_muted);
}

// The counter shows on every question-bearing screen (N OF M from pkjs).
static bool counter_visible(void) {
  return s_q_count > 0 && (s_state == STATE_SELECT || s_state == STATE_CONFIRM ||
                           s_state == STATE_SENDING || s_state == STATE_FEEDBACK);
}

static void render(void) {
  static char cbuf[16];
  if (counter_visible()) snprintf(cbuf, sizeof(cbuf), "%d OF %d", s_q_index, s_q_count);
  else cbuf[0] = '\0';
  set_text(s_counter_layer, cbuf);
  set_text(s_anslabel_layer, "");   // only the confirm screen shows it
  // value font: big on question screens, smaller on the confirm screen
  text_layer_set_font(s_value_layer, fonts_get_system_font(
      s_state == STATE_CONFIRM ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_24_BOLD));
  switch (s_state) {
    case STATE_LOADING:
      set_text(s_title_layer, "");
      set_text(s_value_layer, "Loading…");
      set_text(s_footer1_layer, "");
      set_text(s_footer2_layer, "");
      break;
    case STATE_SELECT:
      set_text(s_title_layer, s_question);
      if (strcmp(s_answer_type, "boolean") == 0) {
        set_text(s_value_layer, s_selection ? "Yes" : "No");
        set_text(s_footer1_layer, "UP / DOWN CHOOSE");
      } else if (strcmp(s_answer_type, "scale") == 0) {
        static char buf[16];
        snprintf(buf, sizeof(buf), "%d", s_selection);
        set_text(s_value_layer, buf);
        set_text(s_footer1_layer, "UP / DOWN VALUE");
      } else {
        set_text(s_value_layer, s_options[s_selection]);
        set_text(s_footer1_layer, "UP / DOWN OPTION");
      }
      set_text(s_footer2_layer, "SELECT TO SEND");
      break;
    case STATE_CONFIRM:
      set_text(s_title_layer, "Confirm?");
      set_text(s_anslabel_layer, "YOUR ANSWER");
      set_text(s_value_layer, s_last_status);
      set_text(s_footer1_layer, "SELECT SUBMITS");
      set_text(s_footer2_layer, "BACK TO EDIT");
      break;
    case STATE_SENDING:
      set_text(s_title_layer, s_question);
      set_text(s_value_layer, "Sending…");
      set_text(s_footer1_layer, "");
      set_text(s_footer2_layer, "");
      break;
    case STATE_FEEDBACK:
      set_text(s_title_layer, s_question);
      set_text(s_value_layer, s_last_status);
      set_text(s_footer1_layer, "");
      set_text(s_footer2_layer, "");
      break;
    case STATE_IDLE:
      set_text(s_title_layer, "");
      set_text(s_value_layer, s_last_status);
      set_text(s_footer1_layer, s_idle_hint);
      set_text(s_footer2_layer, "");
      break;
  }

  // vertical placement of the value text: hug the question on question screens,
  // vertically centered on the idle/loading screens
  int w = layer_get_bounds(window_get_root_layer(s_window)).size.w - 34;
  if (s_state == STATE_CONFIRM) {
    GSize ts = text_layer_get_content_size(s_title_layer);
    int th = (int)ts.h;
    if (th < 10) th = 28;
    int ly = 52 + th + 6;
    layer_set_frame(text_layer_get_layer(s_anslabel_layer), GRect(10, ly, w, 16));
    layer_set_frame(text_layer_get_layer(s_value_layer), GRect(10, ly + 18, w, 30));
  } else if (s_state == STATE_SELECT || s_state == STATE_SENDING ||
             s_state == STATE_FEEDBACK) {
    GSize ts = text_layer_get_content_size(s_title_layer);
    int th = (int)ts.h;
    if (th < 10) th = 28;               // fallback until the layer has laid out
    int vy = 52 + th + 10;
    if (vy > 138) vy = 138;             // keep clear of the footer
    layer_set_frame(text_layer_get_layer(s_value_layer), GRect(10, vy, w, 46));
  } else {
    layer_set_frame(text_layer_get_layer(s_value_layer), GRect(10, 100, w, 46));
  }
}

// Idle/status screen (no question on display): unconfigured, no pending, skipped.
static void show_idle(const char *value, const char *hint) {
  snprintf(s_last_status, sizeof(s_last_status), "%s", value);
  snprintf(s_idle_hint, sizeof(s_idle_hint), "%s", hint);
  s_state = STATE_IDLE;
  render();
}

// ---------------------------------------------------------------------------
// Commands to pkjs

static void send_answer(void) {
  DictionaryIterator *iter;
  AppMessageResult res = app_message_outbox_begin(&iter);
  if (res != APP_MSG_OK) {
    // retry shortly; the question stays pending
    app_timer_register(1000, (AppTimerCallback)send_answer, NULL);
    return;
  }
  dict_write_cstring(iter, MESSAGE_KEY_TYPE, "answer");
  dict_write_cstring(iter, MESSAGE_KEY_INSTANCE_ID, s_instance_id);
  dict_write_cstring(iter, MESSAGE_KEY_ANSWER_TYPE, s_answer_type);
  if (strcmp(s_answer_type, "single_choice") == 0) {
    dict_write_cstring(iter, MESSAGE_KEY_ANSWER_CHOICE, s_options[s_selection]);
  } else {
    dict_write_int32(iter, MESSAGE_KEY_ANSWER_VALUE, s_selection);
  }
  app_message_outbox_send();
}

static void request_next(void) {
  DictionaryIterator *iter;
  AppMessageResult res = app_message_outbox_begin(&iter);
  if (res != APP_MSG_OK) return;
  dict_write_cstring(iter, MESSAGE_KEY_REQUEST, "next");
  app_message_outbox_send();
}

static void show_state_and_next_after(void *data) {
  s_state = STATE_LOADING;
  render();
  request_next();
}

// ---------------------------------------------------------------------------
// Question handling

static void begin_question(DictionaryIterator *iter) {
  Tuple *id = dict_find(iter, MESSAGE_KEY_INSTANCE_ID);
  Tuple *q = dict_find(iter, MESSAGE_KEY_QUESTION);
  Tuple *type = dict_find(iter, MESSAGE_KEY_ANSWER_TYPE);
  if (!id || !q || !type) return;

  snprintf(s_instance_id, sizeof(s_instance_id), "%s", id->value->cstring);
  snprintf(s_question, sizeof(s_question), "%s", q->value->cstring);
  snprintf(s_answer_type, sizeof(s_answer_type), "%s", type->value->cstring);

  s_option_count = 0;
  s_scale_min = 0;
  s_scale_max = 10;
  if (strcmp(s_answer_type, "single_choice") == 0) {
    Tuple *opts = dict_find(iter, MESSAGE_KEY_ANSWER_OPTIONS);
    if (opts) {
      const char *raw = opts->value->cstring;
      const char *start = raw;
      s_option_count = 0;
      for (int i = 0; i <= (int)strlen(raw); i++) {
        if (raw[i] == '\n' || raw[i] == '\0') {
          int len = i - (int)(start - raw);
          if (len > 0) {
            if (len > MAX_OPTION_LEN - 1) len = MAX_OPTION_LEN - 1;
            for (int j = 0; j < len; j++) s_options[s_option_count][j] = start[j];
            s_options[s_option_count][len] = '\0';
            s_option_count++;
            if (s_option_count >= MAX_OPTIONS) break;
          }
          start = raw + i + 1;
        }
      }
    }
  } else if (strcmp(s_answer_type, "scale") == 0) {
    Tuple *min_t = dict_find(iter, MESSAGE_KEY_SCALE_MIN);
    Tuple *max_t = dict_find(iter, MESSAGE_KEY_SCALE_MAX);
    if (min_t) s_scale_min = (int)min_t->value->int32;
    if (max_t) s_scale_max = (int)max_t->value->int32;
  }

  if (strcmp(s_answer_type, "boolean") == 0) s_selection = 0;
  else if (strcmp(s_answer_type, "scale") == 0) s_selection = s_scale_min;
  else s_selection = 0;

  Tuple *qi = dict_find(iter, MESSAGE_KEY_QUESTION_INDEX);
  Tuple *qc = dict_find(iter, MESSAGE_KEY_QUESTION_COUNT);
  s_q_index = qi ? (int)qi->value->int32 : 0;
  s_q_count = qc ? (int)qc->value->int32 : 0;

  s_state = STATE_SELECT;
  render();
}

static void handle_status(DictionaryIterator *iter) {
  Tuple *status = dict_find(iter, MESSAGE_KEY_STATUS_TEXT);
  if (!status) return;
  const char *s = status->value->cstring;
  // Pebble system fonts have no U+2713 glyph — plain ASCII only here.
  if (strcmp(s, "ok") == 0) snprintf(s_last_status, sizeof(s_last_status), "Saved!");
  else if (strcmp(s, "already") == 0) snprintf(s_last_status, sizeof(s_last_status), "Already submitted");
  else if (strcmp(s, "too_late") == 0) snprintf(s_last_status, sizeof(s_last_status), "Too late");
  else snprintf(s_last_status, sizeof(s_last_status), "Retrying…");
  s_state = STATE_FEEDBACK;
  render();
  s_busy = false;
  app_timer_register(900, (AppTimerCallback)show_state_and_next_after, NULL);
}

static void inbox_received(DictionaryIterator *iter, void *context) {
  Tuple *req = dict_find(iter, MESSAGE_KEY_REQUEST);
  Tuple *inst = dict_find(iter, MESSAGE_KEY_INSTANCE_ID);
  Tuple *status = dict_find(iter, MESSAGE_KEY_STATUS_TEXT);

  if (status) {                      // submit result from pkjs
    if (s_state == STATE_SENDING) {
      handle_status(iter);
    } else if (strcmp(status->value->cstring, "error") == 0 &&
               (s_state == STATE_LOADING || s_state == STATE_IDLE)) {
      // initial pending-fetch failed (server unreachable): never hang on Loading
      show_idle("Server unreachable", "UP to retry");
    }
    return;
  }
  if (inst && !s_busy) {             // a new question — only when we're free
    s_busy = true;
    begin_question(iter);
  } else if (req && strcmp(req->value->cstring, "unconfigured") == 0) {
    // no server URL/token configured in the phone-side settings
    show_idle("Not configured", "Set server in phone settings");
  } else if (req && strcmp(req->value->cstring, "idle") == 0) {
    // no pending check-ins from pkjs
    show_idle("No pending", "UP to refresh");
  }

  // theme from the settings page (same pattern as the watchface)
  Tuple *theme = dict_find(iter, MESSAGE_KEY_UI_THEME);
  if (theme) {
    int t = (int)theme->value->int32;
    int old = s_theme;
    apply_colors(t == 1 ? 1 : 0);
    if (old != s_theme) render();
  }
}

// ---------------------------------------------------------------------------
// Buttons

static void adjust(int delta) {
  if (s_state != STATE_SELECT) return;
  if (strcmp(s_answer_type, "boolean") == 0) {
    s_selection = !s_selection;
  } else if (strcmp(s_answer_type, "scale") == 0) {
    s_selection += delta;
    if (s_selection < s_scale_min) s_selection = s_scale_min;
    if (s_selection > s_scale_max) s_selection = s_scale_max;
  } else {
    s_selection += delta;
    if (s_selection < 0) s_selection = s_option_count - 1;
    if (s_selection >= s_option_count) s_selection = 0;
  }
  render();
}

static void up_click(ClickRecognizerRef recognizer, void *context) {
  if (s_state == STATE_SELECT) {
    if (s_busy) {
      adjust(1);
    } else {
      // no question on screen: UP re-checks the server
      request_next();
    }
    return;
  }
  if (s_state == STATE_FEEDBACK || s_state == STATE_LOADING || s_state == STATE_IDLE) {
    request_next();  // manual refresh
  }
}

static void down_click(ClickRecognizerRef recognizer, void *context) {
  if (s_state == STATE_SELECT) adjust(-1);
}

static void select_click(ClickRecognizerRef recognizer, void *context) {
  if (s_state == STATE_SELECT) {
    s_state = STATE_CONFIRM;
    // what will be submitted, shown under "Confirm?"
    if (strcmp(s_answer_type, "boolean") == 0) {
      snprintf(s_last_status, sizeof(s_last_status), s_selection ? "Yes" : "No");
    } else if (strcmp(s_answer_type, "scale") == 0) {
      snprintf(s_last_status, sizeof(s_last_status), "%d", s_selection);
    } else {
      snprintf(s_last_status, sizeof(s_last_status), "%s", s_options[s_selection]);
    }
    render();
  } else if (s_state == STATE_CONFIRM) {
    s_state = STATE_SENDING;
    render();
    send_answer();
  }
}

static void back_click(ClickRecognizerRef recognizer, void *context) {
  if (s_state == STATE_CONFIRM) {   // change your mind: back to selecting
    s_state = STATE_SELECT;
    render();
  } else if (s_state == STATE_SELECT) {  // cancel this question, keep it pending
    s_busy = false;
    show_idle("Skipped", "UP to check again");
  }
}

static void click_config_provider(void *context) {
  window_single_click_subscribe(BUTTON_ID_UP, up_click);
  window_single_click_subscribe(BUTTON_ID_DOWN, down_click);
  window_single_click_subscribe(BUTTON_ID_SELECT, select_click);
  window_single_click_subscribe(BUTTON_ID_BACK, back_click);
}

// ---------------------------------------------------------------------------
// Chrome canvas: button rail + glyphs + header divider + counter underline.
// The rail sits on the right edge, glyph positions hint at the physical
// UP / SELECT / DOWN buttons (middle-right of the watch).

static bool chrome_counter_visible(void) {
  return counter_visible();
}

static void chrome_update_proc(Layer *layer, GContext *ctx) {
  (void)layer;
  GRect bounds = layer_get_bounds(layer);
  // button rail
  graphics_context_set_fill_color(ctx, s_col_panel);
  graphics_fill_rect(ctx, GRect(bounds.size.w - 24, 0, 24, bounds.size.h), 0, GCornerNone);
  // glyphs aligned to the physical buttons, theme-colored
  graphics_context_set_stroke_color(ctx, s_col_accent);
  // up chevron
  for (int o = 0; o < 2; o++) {
    graphics_draw_line(ctx, GPoint(182, 50 + o), GPoint(188, 44 + o));
    graphics_draw_line(ctx, GPoint(188, 44 + o), GPoint(194, 50 + o));
  }
  // check (select / send)
  for (int o = 0; o < 2; o++) {
    graphics_draw_line(ctx, GPoint(181, 113 + o), GPoint(186, 118 + o));
    graphics_draw_line(ctx, GPoint(186, 118 + o), GPoint(195, 106 + o));
  }
  // down chevron
  for (int o = 0; o < 2; o++) {
    graphics_draw_line(ctx, GPoint(182, 178 + o), GPoint(188, 184 + o));
    graphics_draw_line(ctx, GPoint(188, 184 + o), GPoint(194, 178 + o));
  }
  // thin divider under the header (content area only)
  graphics_context_set_fill_color(ctx, s_col_muted);
  graphics_fill_rect(ctx, GRect(0, 27, bounds.size.w - 24, 1), 0, GCornerNone);
  // short accent bar under the question counter
  if (chrome_counter_visible()) {
    graphics_context_set_fill_color(ctx, s_col_accent);
    graphics_fill_rect(ctx, GRect(10, 46, 26, 3), 0, GCornerNone);
  }
}

// ---------------------------------------------------------------------------
// 24h clock (minute tick)

static void update_clock(void) {
  time_t now = time(NULL);
  struct tm *tm = localtime(&now);
  strftime(s_clock_buf, sizeof(s_clock_buf), "%H:%M", tm);
  set_text(s_clock_layer, s_clock_buf);
}

static void tick_handler(struct tm *tick_time, TimeUnits units) {
  (void)tick_time;
  (void)units;
  update_clock();
}

// ---------------------------------------------------------------------------
// Window lifecycle

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(root);
  apply_colors(s_theme);

  // chrome canvas under everything
  s_chrome_layer = layer_create(bounds);
  layer_set_update_proc(s_chrome_layer, chrome_update_proc);
  layer_add_child(root, s_chrome_layer);

  // header: GUAI wordmark (accent) + 24h clock (hours and minutes identical)
  s_wordmark_layer = text_layer_create(GRect(8, 3, 60, 22));
  text_layer_set_background_color(s_wordmark_layer, GColorClear);
  text_layer_set_text_color(s_wordmark_layer, s_col_accent);
  text_layer_set_font(s_wordmark_layer, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD));
  text_layer_set_text_alignment(s_wordmark_layer, GTextAlignmentLeft);
  set_text(s_wordmark_layer, "GUAI");

  s_clock_layer = text_layer_create(GRect(114, 4, 54, 22));
  text_layer_set_background_color(s_clock_layer, GColorClear);
  text_layer_set_text_color(s_clock_layer, s_col_fg);
  text_layer_set_font(s_clock_layer, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD));
  text_layer_set_text_alignment(s_clock_layer, GTextAlignmentRight);

  // question counter: "N OF M" in accent with a short teal underline bar
  s_counter_layer = text_layer_create(GRect(10, 28, 100, 16));
  text_layer_set_background_color(s_counter_layer, GColorClear);
  text_layer_set_text_color(s_counter_layer, s_col_accent);
  text_layer_set_font(s_counter_layer, fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD));
  text_layer_set_text_alignment(s_counter_layer, GTextAlignmentLeft);

  // "YOUR ANSWER" label on the confirm screen (hidden elsewhere)
  s_anslabel_layer = text_layer_create(GRect(10, 80, bounds.size.w - 34, 16));
  text_layer_set_background_color(s_anslabel_layer, GColorClear);
  text_layer_set_text_color(s_anslabel_layer, s_col_muted);
  text_layer_set_font(s_anslabel_layer, fonts_get_system_font(FONT_KEY_GOTHIC_14));
  text_layer_set_text_alignment(s_anslabel_layer, GTextAlignmentCenter);

  // question: large bold white, wrapping to up to three lines
  s_title_layer = text_layer_create(GRect(10, 52, bounds.size.w - 34, 84));
  text_layer_set_background_color(s_title_layer, GColorClear);
  text_layer_set_text_color(s_title_layer, s_col_fg);
  text_layer_set_font(s_title_layer, fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD));
  text_layer_set_text_alignment(s_title_layer, GTextAlignmentLeft);
  text_layer_set_overflow_mode(s_title_layer, GTextOverflowModeWordWrap);

  // current value / interim status text (two lines max, above the footer)
  s_value_layer = text_layer_create(GRect(10, 138, bounds.size.w - 34, 46));
  text_layer_set_background_color(s_value_layer, GColorClear);
  text_layer_set_text_color(s_value_layer, s_col_fg);
  text_layer_set_font(s_value_layer, fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD));
  text_layer_set_text_alignment(s_value_layer, GTextAlignmentCenter);
  text_layer_set_overflow_mode(s_value_layer, GTextOverflowModeWordWrap);

  // footer: two muted caps hints
  s_footer1_layer = text_layer_create(GRect(10, 190, bounds.size.w - 34, 17));
  text_layer_set_background_color(s_footer1_layer, GColorClear);
  text_layer_set_text_color(s_footer1_layer, s_col_muted);
  text_layer_set_font(s_footer1_layer, fonts_get_system_font(FONT_KEY_GOTHIC_14));
  text_layer_set_text_alignment(s_footer1_layer, GTextAlignmentLeft);

  s_footer2_layer = text_layer_create(GRect(10, 208, bounds.size.w - 34, 17));
  text_layer_set_background_color(s_footer2_layer, GColorClear);
  text_layer_set_text_color(s_footer2_layer, s_col_muted);
  text_layer_set_font(s_footer2_layer, fonts_get_system_font(FONT_KEY_GOTHIC_14));
  text_layer_set_text_alignment(s_footer2_layer, GTextAlignmentLeft);

  layer_add_child(root, text_layer_get_layer(s_wordmark_layer));
  layer_add_child(root, text_layer_get_layer(s_clock_layer));
  layer_add_child(root, text_layer_get_layer(s_counter_layer));
  layer_add_child(root, text_layer_get_layer(s_anslabel_layer));
  layer_add_child(root, text_layer_get_layer(s_title_layer));
  layer_add_child(root, text_layer_get_layer(s_value_layer));
  layer_add_child(root, text_layer_get_layer(s_footer1_layer));
  layer_add_child(root, text_layer_get_layer(s_footer2_layer));

  update_clock();
  tick_timer_service_subscribe(MINUTE_UNIT, tick_handler);
  render();
}

static void window_unload(Window *window) {
  tick_timer_service_unsubscribe();
  layer_destroy(s_chrome_layer);
  text_layer_destroy(s_wordmark_layer);
  text_layer_destroy(s_clock_layer);
  text_layer_destroy(s_counter_layer);
  text_layer_destroy(s_anslabel_layer);
  text_layer_destroy(s_title_layer);
  text_layer_destroy(s_value_layer);
  text_layer_destroy(s_footer1_layer);
  text_layer_destroy(s_footer2_layer);
}

static void init(void) {
  s_window = window_create();
  apply_colors(s_theme);
  window_set_window_handlers(s_window, (WindowHandlers){
    .load = window_load,
    .unload = window_unload,
  });
  window_set_click_config_provider(s_window, click_config_provider);
  window_stack_push(s_window, true);

  app_message_register_inbox_received(inbox_received);
  app_message_open(512, 512);

  s_state = STATE_LOADING;
  render();
}

static void deinit(void) {
  window_destroy(s_window);
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}