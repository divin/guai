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
static TextLayer *s_title_layer;
static TextLayer *s_value_layer;
static TextLayer *s_hint_layer;

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
  if (s_title_layer) text_layer_set_text_color(s_title_layer, s_col_fg);
  if (s_value_layer) text_layer_set_text_color(s_value_layer, s_col_fg);
  if (s_hint_layer) text_layer_set_text_color(s_hint_layer, s_col_muted);
}

static void render(void) {
  switch (s_state) {
    case STATE_LOADING:
      set_text(s_title_layer, "GUAI");
      set_text(s_value_layer, "Loading…");
      set_text(s_hint_layer, "");
      break;
    case STATE_SELECT:
      set_text(s_title_layer, s_question);
      if (strcmp(s_answer_type, "boolean") == 0) {
        set_text(s_value_layer, s_selection ? "Yes" : "No");
        set_text(s_hint_layer, "UP/DOWN toggle · SELECT confirm");
      } else if (strcmp(s_answer_type, "scale") == 0) {
        static char buf[16];
        snprintf(buf, sizeof(buf), "%d", s_selection);
        set_text(s_value_layer, buf);
        set_text(s_hint_layer, "UP/DOWN value · SELECT confirm");
      } else {
        set_text(s_value_layer, s_options[s_selection]);
        set_text(s_hint_layer, "UP/DOWN option · SELECT confirm");
      }
      break;
    case STATE_CONFIRM:
      set_text(s_title_layer, "Submit?");
      set_text(s_value_layer, s_last_status);
      set_text(s_hint_layer, "SELECT yes · BACK no");
      break;
    case STATE_SENDING:
      set_text(s_title_layer, s_question);
      set_text(s_value_layer, "Sending…");
      set_text(s_hint_layer, "");
      break;
    case STATE_FEEDBACK:
      set_text(s_title_layer, s_question);
      set_text(s_value_layer, s_last_status);
      set_text(s_hint_layer, "");
      break;
    case STATE_IDLE:
      set_text(s_title_layer, "GUAI Check-In");
      set_text(s_value_layer, s_last_status);
      set_text(s_hint_layer, s_idle_hint);
      break;
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

  s_state = STATE_SELECT;
  render();
}

static void handle_status(DictionaryIterator *iter) {
  Tuple *status = dict_find(iter, MESSAGE_KEY_STATUS_TEXT);
  if (!status) return;
  const char *s = status->value->cstring;
  if (strcmp(s, "ok") == 0) snprintf(s_last_status, sizeof(s_last_status), "Saved ✓");
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
    snprintf(s_last_status, sizeof(s_last_status),
             strcmp(s_answer_type, "boolean") == 0
                 ? (s_selection ? "Yes" : "No")
                 : (strcmp(s_answer_type, "scale") == 0 ? "the value" : "your choice"));
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
// Window lifecycle

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(root);
  apply_colors(s_theme);

  s_title_layer = text_layer_create(GRect(12, 0, bounds.size.w - 24, 84));
  text_layer_set_background_color(s_title_layer, GColorClear);
  text_layer_set_text_color(s_title_layer, GColorWhite);
  text_layer_set_font(s_title_layer, fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD));
  text_layer_set_text_alignment(s_title_layer, GTextAlignmentLeft);
  text_layer_set_overflow_mode(s_title_layer, GTextOverflowModeWordWrap);

  s_value_layer = text_layer_create(GRect(12, 78, bounds.size.w - 24, 62));
  text_layer_set_background_color(s_value_layer, GColorClear);
  text_layer_set_text_color(s_value_layer, GColorWhite);
  text_layer_set_font(s_value_layer, fonts_get_system_font(FONT_KEY_BITHAM_30_BLACK));
  text_layer_set_text_alignment(s_value_layer, GTextAlignmentCenter);
  text_layer_set_overflow_mode(s_value_layer, GTextOverflowModeWordWrap);

  s_hint_layer = text_layer_create(GRect(12, 138, bounds.size.w - 24, 30));
  text_layer_set_background_color(s_hint_layer, GColorClear);
  text_layer_set_text_color(s_hint_layer, s_col_muted);
  text_layer_set_font(s_hint_layer, fonts_get_system_font(FONT_KEY_GOTHIC_14));
  text_layer_set_text_alignment(s_hint_layer, GTextAlignmentCenter);

  layer_add_child(root, text_layer_get_layer(s_title_layer));
  layer_add_child(root, text_layer_get_layer(s_value_layer));
  layer_add_child(root, text_layer_get_layer(s_hint_layer));

  render();
}

static void window_unload(Window *window) {
  text_layer_destroy(s_title_layer);
  text_layer_destroy(s_value_layer);
  text_layer_destroy(s_hint_layer);
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