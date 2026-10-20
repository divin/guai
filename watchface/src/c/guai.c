#include <pebble.h>

// GUAI watchface — dashboard layout: date + battery icon/%, big time, heart rate,
// step-goal progress bar, and a three-column stats panel whose slots are
// user-configurable (steps / sleep / active / weather). Light/dark theme selectable
// in the settings page (two icon variations per theme). Health data syncs to the
// GUAI server via pkjs exactly as before (AppMessage snapshots).

static Window *s_window;
static Layer *s_panel_layer;      // bottom stats panel (bg + dividers + bar)
static Layer *s_bar_layer;        // step-goal progress bar
static TextLayer *s_date_layer;
static TextLayer *s_time_layer;
static TextLayer *s_batt_text_layer;
static TextLayer *s_hr_layer;
static TextLayer *s_bpm_unit_layer;
static GFont s_time_font;
static TextLayer *s_stat_num[3];
static TextLayer *s_stat_lbl[3];
static BitmapLayer *s_icon_hr;
static BitmapLayer *s_icon_stat[3];
static GBitmap *s_img_hr;
static GBitmap *s_img_metric[2][3];  // [theme][steps/sleep/active]
static BitmapLayer *s_batt_icon_layer;
static GBitmap *s_img_batt[2][5];    // [theme][level]
static GBitmap *s_img_wx;            // condition icon for the weather slot
static int s_slots[3] = {0, 1, 2};   // 0=steps 1=sleep 2=active 3=weather
static bool s_wx_valid = false;
static int s_wx_cond = 0;
static int s_wx_temp_x10 = 0;

#define STEP_GOAL_DEFAULT 10000
#define STEP_GOAL_MIN 1000
#define STEP_GOAL_MAX 100000

// Theme state and palette: 0 = dark (default), 1 = light.
static int s_theme = 0;
static GColor s_col_bg;
static GColor s_col_fg;        // primary text
static GColor s_col_muted;     // secondary text (units, labels)
static GColor s_col_panel;     // bottom panel background
static GColor s_col_divider;   // panel dividers
static GColor s_col_track;     // progress-bar track
static GColor s_col_accent;    // progress-bar fill

// Per-theme icon resources: [0] = dark, [1] = light.
static const int METRIC_RESOURCE_IDS[2][3] = {
  { RESOURCE_ID_ICON_STEPS, RESOURCE_ID_ICON_SLEEP, RESOURCE_ID_ICON_ACTIVE },
  { RESOURCE_ID_ICON_STEPS_LIGHT, RESOURCE_ID_ICON_SLEEP_LIGHT, RESOURCE_ID_ICON_ACTIVE_LIGHT },
};
static const int BATT_RESOURCE_IDS[2][5] = {
  { RESOURCE_ID_ICON_BATT_EMPTY, RESOURCE_ID_ICON_BATT_LOW,
    RESOURCE_ID_ICON_BATT_MID, RESOURCE_ID_ICON_BATT_FULL, RESOURCE_ID_ICON_BATT_CHARGING },
  { RESOURCE_ID_ICON_BATT_EMPTY_LIGHT, RESOURCE_ID_ICON_BATT_LOW_LIGHT,
    RESOURCE_ID_ICON_BATT_MID_LIGHT, RESOURCE_ID_ICON_BATT_FULL_LIGHT,
    RESOURCE_ID_ICON_BATT_CHARGING_LIGHT },
};

// WEATHER_COND code -> icon (fog maps to cloudy; the pack has no fog icon).
static const int WX_RESOURCE_IDS[2][8] = {
  { RESOURCE_ID_ICON_WX_CLEAR, RESOURCE_ID_ICON_WX_PARTLY, RESOURCE_ID_ICON_WX_CLOUDY,
    RESOURCE_ID_ICON_WX_DRIZZLE, RESOURCE_ID_ICON_WX_RAIN, RESOURCE_ID_ICON_WX_SNOW,
    RESOURCE_ID_ICON_WX_STORM, RESOURCE_ID_ICON_WX_CLOUDY },
  { RESOURCE_ID_ICON_WX_CLEAR_LIGHT, RESOURCE_ID_ICON_WX_PARTLY_LIGHT,
    RESOURCE_ID_ICON_WX_CLOUDY_LIGHT, RESOURCE_ID_ICON_WX_DRIZZLE_LIGHT,
    RESOURCE_ID_ICON_WX_RAIN_LIGHT, RESOURCE_ID_ICON_WX_SNOW_LIGHT,
    RESOURCE_ID_ICON_WX_STORM_LIGHT, RESOURCE_ID_ICON_WX_CLOUDY_LIGHT },
};

static int s_steps = 0;
static int s_sleep_min = 0;
static int s_active_min = 0;
static int s_step_goal = STEP_GOAL_DEFAULT;  // user-adjustable via settings

static void apply_theme(int theme);

// ---------------------------------------------------------------------------
// Health -> pkjs sync (unchanged protocol, spec/integration.md)

#ifdef PBL_HEALTH
static void send_health(void) {
  DictionaryIterator *iter;
  AppMessageResult res = app_message_outbox_begin(&iter);
  if (res != APP_MSG_OK) return;

  const int32_t now = (int32_t)time(NULL);
  dict_write_cstring(iter, MESSAGE_KEY_TYPE, "activity");
  dict_write_int32(iter, MESSAGE_KEY_TIMESTAMP, now);
  dict_write_int32(iter, MESSAGE_KEY_STEPS,
                    (int32_t)health_service_sum_today(HealthMetricStepCount));
  dict_write_int32(iter, MESSAGE_KEY_DISTANCE_METERS,
                    (int32_t)(health_service_sum_today(HealthMetricWalkedDistanceMeters) * 10));
  dict_write_int32(iter, MESSAGE_KEY_ACTIVE_SECONDS,
                    (int32_t)health_service_sum_today(HealthMetricActiveSeconds));
  dict_write_int32(iter, MESSAGE_KEY_RESTING_KCAL,
                    (int32_t)(health_service_sum_today(HealthMetricRestingKCalories) * 10));
  dict_write_int32(iter, MESSAGE_KEY_ACTIVE_KCAL,
                    (int32_t)(health_service_sum_today(HealthMetricActiveKCalories) * 10));

  time_t now_t = time(NULL);
  HealthServiceAccessibilityMask mask =
      health_service_metric_accessible(HealthMetricHeartRateBPM, now_t, now_t);
  if (mask & HealthServiceAccessibilityMaskAvailable) {
    dict_write_int32(iter, MESSAGE_KEY_HR_BPM,
                     (int32_t)health_service_peek_current_value(HealthMetricHeartRateBPM));
    dict_write_int32(iter, MESSAGE_KEY_HR_RAW_BPM,
                     (int32_t)health_service_peek_current_value(HealthMetricHeartRateRawBPM));
  }

  app_message_outbox_send();
}

static void send_sleep(void) {
  time_t night_start = time_start_of_today() - (24 * SECONDS_PER_HOUR);
  time_t today_start = time_start_of_today();
  HealthValue sleep_secs =
      health_service_sum(HealthMetricSleepSeconds, night_start, today_start);
  HealthValue restful_secs =
      health_service_sum(HealthMetricSleepRestfulSeconds, night_start, today_start);

  DictionaryIterator *iter;
  AppMessageResult res = app_message_outbox_begin(&iter);
  if (res != APP_MSG_OK) return;
  dict_write_cstring(iter, MESSAGE_KEY_TYPE, "sleep");
  dict_write_int32(iter, MESSAGE_KEY_TIMESTAMP, (int32_t)night_start);
  dict_write_int32(iter, MESSAGE_KEY_SLEEP_SECONDS, (int32_t)sleep_secs);
  dict_write_int32(iter, MESSAGE_KEY_SLEEP_RESTFUL_SECONDS, (int32_t)restful_secs);
  app_message_outbox_send();
}

static void request_sleep(void) {
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) return;
  dict_write_cstring(iter, MESSAGE_KEY_REQUEST, "sleep_request");
  app_message_outbox_send();
}
#endif

// ---------------------------------------------------------------------------
// Drawing: progress bar, stats panel

static void panel_update_proc(Layer *layer, GContext *ctx) {
  GRect bounds = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, s_col_panel);
  graphics_fill_rect(ctx, bounds, 0, GCornerNone);
  // column dividers
  graphics_context_set_stroke_color(ctx, s_col_divider);
  int col_w = bounds.size.w / 3;
  for (int i = 1; i <= 2; i++) {
    graphics_draw_line(ctx, GPoint(i * col_w, 4), GPoint(i * col_w, bounds.size.h - 4));
  }
}

// Step-goal progress bar drawn as a thin layer above the panel.
static void bar_layer_update_proc(Layer *layer, GContext *ctx) {
  GRect bounds = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, s_col_track);
  graphics_fill_rect(ctx, bounds, 0, GCornerNone);
  graphics_context_set_fill_color(ctx, s_col_accent);
  int w = bounds.size.w * (s_steps > s_step_goal ? s_step_goal : s_steps) / s_step_goal;
  if (w > 0) graphics_fill_rect(ctx, GRect(0, 0, w, bounds.size.h), 0, GCornerNone);
}

// ---------------------------------------------------------------------------
// UI updates

static void update_stats(void) {
#ifdef PBL_HEALTH
  s_steps = (int)health_service_sum_today(HealthMetricStepCount);
  time_t night_start = time_start_of_today() - (24 * SECONDS_PER_HOUR);
  HealthValue sleep_secs = health_service_sum(HealthMetricSleepSeconds, night_start, time_start_of_today());
  s_sleep_min = (int)(sleep_secs / 60);
  s_active_min = (int)(health_service_sum_today(HealthMetricActiveSeconds) / 60);
#endif
}

static void update_hr(void) {
#ifdef PBL_HEALTH
  time_t now = time(NULL);
  HealthServiceAccessibilityMask mask =
      health_service_metric_accessible(HealthMetricHeartRateBPM, now, now);
  if (mask & HealthServiceAccessibilityMaskAvailable) {
    HealthValue hr = health_service_peek_current_value(HealthMetricHeartRateBPM);
    static char buf[16];
    snprintf(buf, sizeof(buf), "%d", (int)hr);
    text_layer_set_text(s_hr_layer, buf);
  } else {
    text_layer_set_text(s_hr_layer, "--");
  }
#endif
}

// Weather slot values: temperature rounding from the x10 wire format.
static int round_tenths(int x10) {
  return (x10 >= 0) ? (x10 + 5) / 10 : -((-x10 + 5) / 10);
}

// ---------------------------------------------------------------------------
// Configurable bottom panel: each slot renders steps / sleep / active / weather

static void set_column(int col, GBitmap *icon, const char *num, const char *label) {
  bitmap_layer_set_bitmap(s_icon_stat[col], icon);
  text_layer_set_text(s_stat_num[col], num);
  text_layer_set_text(s_stat_lbl[col], label);
}

static void update_panel(void) {
  static char buf_steps[16];
  static char buf_sleep[16];
  static char buf_active[16];
  static char buf_wx[16];
  if (s_steps >= 1000) {
    snprintf(buf_steps, sizeof(buf_steps), "%d,%03d", s_steps / 1000, s_steps % 1000);
  } else {
    snprintf(buf_steps, sizeof(buf_steps), "%d", s_steps);
  }
  snprintf(buf_sleep, sizeof(buf_sleep), "%dh%02d", s_sleep_min / 60, s_sleep_min % 60);
  snprintf(buf_active, sizeof(buf_active), "%d", s_active_min);

  for (int i = 0; i < 3; i++) {
    GBitmap *icon;
    const char *num;
    const char *label;
    switch (s_slots[i]) {
      case 3:  // weather
        snprintf(buf_wx, sizeof(buf_wx), "%d°", round_tenths(s_wx_temp_x10));
        icon = s_wx_valid ? s_img_wx : NULL;
        num = s_wx_valid ? buf_wx : "--";
        label = "WEATHER";
        break;
      case 1:
        icon = s_img_metric[s_theme][1];
        num = buf_sleep;
        label = "SLEEP";
        break;
      case 2:
        icon = s_img_metric[s_theme][2];
        num = buf_active;
        label = "ACTIVE";
        break;
      default:
        icon = s_img_metric[s_theme][0];
        num = buf_steps;
        label = "STEPS";
        break;
    }
    set_column(i, icon, num, label);
  }
  layer_mark_dirty(s_panel_layer);
}

static void update_time(void) {
  time_t temp = time(NULL);
  struct tm *t = localtime(&temp);

  static char s_date_buf[20];
  strftime(s_date_buf, sizeof(s_date_buf), "%a %d %b", t);
  for (char *p = s_date_buf; *p; ++p) if (*p >= 'a' && *p <= 'z') *p -= 32;
  text_layer_set_text(s_date_layer, s_date_buf);

  static char s_time_buf[8];
  strftime(s_time_buf, sizeof(s_time_buf), "%H:%M", t);  // always 24h
  text_layer_set_text(s_time_layer, s_time_buf);
}

#ifdef PBL_HEALTH
static void health_handler(HealthEventType event, void *context) {
  update_hr();
  update_stats();
  update_panel();
  send_health();
}
#endif

static int battery_icon_index(const BatteryChargeState state) {
  if (state.is_charging) return 4;              // ICON_BATT_CHARGING
  if (state.charge_percent >= 80) return 3;     // ICON_BATT_FULL
  if (state.charge_percent >= 45) return 2;     // ICON_BATT_MID
  if (state.charge_percent >= 20) return 1;     // ICON_BATT_LOW
  return 0;                                     // ICON_BATT_EMPTY
}

static void battery_handler(BatteryChargeState state) {
  bitmap_layer_set_bitmap(s_batt_icon_layer, s_img_batt[s_theme][battery_icon_index(state)]);
  static char buf[6];
  snprintf(buf, sizeof(buf), "%d%%", state.charge_percent);
  text_layer_set_text(s_batt_text_layer, buf);
}

static void tick_handler(struct tm *tick_time, TimeUnits units_changed) {
  update_time();
  if (units_changed & MINUTE_UNIT) {
    update_stats();
    update_hr();
    update_panel();
#ifdef PBL_HEALTH
    send_health();
#endif
  }
}

static void inbox_received(DictionaryIterator *iter, void *context) {
  Tuple *request = dict_find(iter, MESSAGE_KEY_REQUEST);
  if (request && strcmp(request->value->cstring, "sleep_request") == 0) {
#ifdef PBL_HEALTH
    send_sleep();
#endif
  }

  // weather update (server data only): stores state, panel re-renders per slot
  Tuple *wcond = dict_find(iter, MESSAGE_KEY_WEATHER_COND);
  if (wcond) {
    int cond = (int)wcond->value->int32;
    Tuple *wtemp = dict_find(iter, MESSAGE_KEY_WEATHER_TEMP_C);
    if (cond >= 0 && cond < 8) {
      s_wx_valid = true;
      s_wx_cond = cond;
      s_wx_temp_x10 = wtemp ? (int)wtemp->value->int32 : 0;
      gbitmap_destroy(s_img_wx);
      s_img_wx = gbitmap_create_with_resource(WX_RESOURCE_IDS[s_theme][cond]);
    } else {
      s_wx_valid = false;
      gbitmap_destroy(s_img_wx);
      s_img_wx = NULL;
    }
  }

  // theme switch from the settings page
  Tuple *theme = dict_find(iter, MESSAGE_KEY_UI_THEME);
  if (theme) {
    apply_theme((int)theme->value->int32);
  }

  // panel slot assignment from the settings page
  bool slots_changed = false;
  const int slot_keys[3] = {
    MESSAGE_KEY_PANEL_SLOT_A, MESSAGE_KEY_PANEL_SLOT_B, MESSAGE_KEY_PANEL_SLOT_C,
  };
  for (int i = 0; i < 3; i++) {
    Tuple *slot = dict_find(iter, slot_keys[i]);
    if (slot) {
      int v = (int)slot->value->int32;
      s_slots[i] = (v >= 0 && v <= 3) ? v : 0;
      slots_changed = true;
    }
  }

  // daily step goal from the settings page (drives the progress bar)
  Tuple *goal = dict_find(iter, MESSAGE_KEY_STEP_GOAL);
  if (goal) {
    int g = (int)goal->value->int32;
    s_step_goal = (g < STEP_GOAL_MIN) ? STEP_GOAL_MIN : (g > STEP_GOAL_MAX) ? STEP_GOAL_MAX : g;
    layer_mark_dirty(s_bar_layer);
  }

  if (wcond || theme || slots_changed) {
    update_panel();
  }
}

// ---------------------------------------------------------------------------
// Layout helpers + theme

static TextLayer *text_layer_new(GRect frame, const char *font, GColor color, GTextAlignment align) {
  TextLayer *layer = text_layer_create(frame);
  text_layer_set_background_color(layer, GColorClear);
  text_layer_set_text_color(layer, color);
  text_layer_set_font(layer, fonts_get_system_font(font));
  text_layer_set_text_alignment(layer, align);
  return layer;
}

static void apply_colors(int theme) {
  if (theme == 1) {  // light
    s_col_bg = GColorWhite;
    s_col_fg = GColorFromHEX(0x1B1F27);
    s_col_muted = GColorFromHEX(0x5A6170);
    s_col_panel = GColorFromHEX(0xF0F1F4);
    s_col_divider = GColorFromHEX(0x9AA3AD);
    s_col_track = GColorFromHEX(0xC9CDD6);
    s_col_accent = GColorFromHEX(0x1E9C82);
  } else {           // dark (default)
    s_col_bg = GColorBlack;
    s_col_fg = GColorWhite;
    s_col_muted = GColorFromHEX(0x9AA3AD);
    s_col_panel = GColorFromHEX(0x141C33);
    s_col_divider = GColorFromHEX(0x9AA3AD);
    s_col_track = GColorDarkGray;
    s_col_accent = GColorFromHEX(0x36B79F);
  }
}

static void apply_theme_to_layers(void) {
  window_set_background_color(s_window, s_col_bg);
  text_layer_set_text_color(s_date_layer, s_col_fg);
  text_layer_set_text_color(s_time_layer, s_col_fg);
  text_layer_set_text_color(s_batt_text_layer, s_col_fg);
  text_layer_set_text_color(s_hr_layer, s_col_fg);
  text_layer_set_text_color(s_bpm_unit_layer, s_col_muted);
  for (int i = 0; i < 3; i++) {
    text_layer_set_text_color(s_stat_num[i], s_col_fg);
    text_layer_set_text_color(s_stat_lbl[i], s_col_muted);
  }
  layer_mark_dirty(s_bar_layer);
  layer_mark_dirty(s_panel_layer);
}

// Full theme switch: colors + icon set swap (also re-renders the panel and battery).
static void apply_theme(int theme) {
  s_theme = (theme == 1) ? 1 : 0;
  apply_colors(s_theme);
  apply_theme_to_layers();
  gbitmap_destroy(s_img_hr);
  s_img_hr = gbitmap_create_with_resource(s_theme == 1 ? RESOURCE_ID_ICON_HEARTBEAT_LIGHT
                                                       : RESOURCE_ID_ICON_HEARTBEAT);
  bitmap_layer_set_bitmap(s_icon_hr, s_img_hr);
  if (s_wx_valid) {
    gbitmap_destroy(s_img_wx);
    s_img_wx = gbitmap_create_with_resource(WX_RESOURCE_IDS[s_theme][s_wx_cond]);
  }
  update_panel();
  battery_handler(battery_state_service_peek());
}

static void main_window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(root);
  const int W = bounds.size.w;
  apply_colors(s_theme);

  s_date_layer = text_layer_new(GRect(4, 3, 130, 24), FONT_KEY_GOTHIC_18_BOLD, s_col_fg, GTextAlignmentLeft);
  s_time_layer = text_layer_new(GRect(2, 26, W, 64), FONT_KEY_GOTHIC_28_BOLD, s_col_fg, GTextAlignmentCenter);
  s_time_font = fonts_load_custom_font(resource_get_handle(RESOURCE_ID_FONT_TIME_56));
  text_layer_set_font(s_time_layer, s_time_font);

  layer_add_child(root, text_layer_get_layer(s_date_layer));
  layer_add_child(root, text_layer_get_layer(s_time_layer));

  // battery icon + percent (top-right), with breathing room between the two
  s_batt_icon_layer = bitmap_layer_create(GRect(126, 0, 32, 32));
  bitmap_layer_set_compositing_mode(s_batt_icon_layer, GCompOpSet);
  layer_add_child(root, bitmap_layer_get_layer(s_batt_icon_layer));
  for (int t = 0; t < 2; t++) {
    for (int i = 0; i < 5; i++) {
      s_img_batt[t][i] = gbitmap_create_with_resource(BATT_RESOURCE_IDS[t][i]);
    }
  }
  s_batt_text_layer = text_layer_new(GRect(160, 6, 38, 24), FONT_KEY_GOTHIC_14, s_col_fg, GTextAlignmentLeft);
  layer_add_child(root, text_layer_get_layer(s_batt_text_layer));

  // heart-rate row: icon + BPM number + "BPM" unit, centered as a block
  s_img_hr = gbitmap_create_with_resource(s_theme == 1 ? RESOURCE_ID_ICON_HEARTBEAT_LIGHT
                                                       : RESOURCE_ID_ICON_HEARTBEAT);
  s_icon_hr = bitmap_layer_create(GRect(44, 98, 32, 32));
  bitmap_layer_set_bitmap(s_icon_hr, s_img_hr);
  bitmap_layer_set_compositing_mode(s_icon_hr, GCompOpSet);
  layer_add_child(root, bitmap_layer_get_layer(s_icon_hr));
  s_hr_layer = text_layer_new(GRect(78, 98, 48, 30), FONT_KEY_GOTHIC_24_BOLD, s_col_fg, GTextAlignmentCenter);
  layer_add_child(root, text_layer_get_layer(s_hr_layer));
  s_bpm_unit_layer = text_layer_new(GRect(126, 106, 48, 20), FONT_KEY_GOTHIC_14, s_col_muted, GTextAlignmentLeft);
  text_layer_set_text(s_bpm_unit_layer, "BPM");
  layer_add_child(root, text_layer_get_layer(s_bpm_unit_layer));

  // step-goal progress bar
  s_bar_layer = layer_create(GRect(0, 146, W, 6));
  layer_set_update_proc(s_bar_layer, bar_layer_update_proc);
  layer_add_child(root, s_bar_layer);

  // bottom stats panel: 3 configurable columns (assignment via the settings page)
  s_panel_layer = layer_create(GRect(0, 152, W, bounds.size.h - 152));
  layer_set_update_proc(s_panel_layer, panel_update_proc);
  layer_add_child(root, s_panel_layer);

  const GRect icon_frames[3] = {
    GRect(18, 2, 32, 32), GRect(84, 2, 32, 32), GRect(150, 2, 32, 32)
  };
  const int num_x[3] = {2, 68, 134};
  const int num_w[3] = {64, 64, 64};

  for (int i = 0; i < 3; i++) {
    for (int t = 0; t < 2; t++) {
      s_img_metric[t][i] = gbitmap_create_with_resource(METRIC_RESOURCE_IDS[t][i]);
    }
    s_icon_stat[i] = bitmap_layer_create(GRect(icon_frames[i].origin.x, icon_frames[i].origin.y,
                                               icon_frames[i].size.w, icon_frames[i].size.h));
    bitmap_layer_set_compositing_mode(s_icon_stat[i], GCompOpSet);
    layer_add_child(s_panel_layer, bitmap_layer_get_layer(s_icon_stat[i]));

    s_stat_num[i] = text_layer_new(GRect(num_x[i], 34, num_w[i], 24),
                                   FONT_KEY_GOTHIC_18_BOLD, s_col_fg, GTextAlignmentCenter);
    layer_add_child(s_panel_layer, text_layer_get_layer(s_stat_num[i]));
    s_stat_lbl[i] = text_layer_new(GRect(num_x[i], 58, num_w[i], 16),
                                   FONT_KEY_GOTHIC_14, s_col_muted, GTextAlignmentCenter);
    layer_add_child(s_panel_layer, text_layer_get_layer(s_stat_lbl[i]));
  }

  apply_theme_to_layers();
  update_time();
  update_stats();
  update_hr();
  update_panel();
}

static void main_window_unload(Window *window) {
  text_layer_destroy(s_date_layer);
  text_layer_destroy(s_time_layer);
  text_layer_destroy(s_batt_text_layer);
  fonts_unload_custom_font(s_time_font);
  text_layer_destroy(s_hr_layer);
  text_layer_destroy(s_bpm_unit_layer);
  layer_destroy(s_bar_layer);
  layer_destroy(s_panel_layer);
  if (s_img_wx) {
    gbitmap_destroy(s_img_wx);
  }
  bitmap_layer_destroy(s_batt_icon_layer);
  for (int t = 0; t < 2; t++) {
    for (int i = 0; i < 5; i++) {
      gbitmap_destroy(s_img_batt[t][i]);
    }
  }
  bitmap_layer_destroy(s_icon_hr);
  gbitmap_destroy(s_img_hr);
  for (int i = 0; i < 3; i++) {
    bitmap_layer_destroy(s_icon_stat[i]);
    text_layer_destroy(s_stat_num[i]);
    text_layer_destroy(s_stat_lbl[i]);
  }
  for (int t = 0; t < 2; t++) {
    for (int i = 0; i < 3; i++) {
      gbitmap_destroy(s_img_metric[t][i]);
    }
  }
}

static void init(void) {
  s_window = window_create();
  window_set_background_color(s_window, GColorBlack);
  window_set_window_handlers(s_window, (WindowHandlers){
    .load = main_window_load,
    .unload = main_window_unload
  });
  window_stack_push(s_window, true);

  tick_timer_service_subscribe(MINUTE_UNIT | DAY_UNIT, tick_handler);
  battery_state_service_subscribe(battery_handler);
  battery_handler(battery_state_service_peek());  // set icon + % immediately at launch

#ifdef PBL_HEALTH
  health_service_events_subscribe(health_handler, NULL);
#endif

  const int inbound_size = 256;
  const int outbound_size = 256;
  app_message_open(inbound_size, outbound_size);
  app_message_register_inbox_received(inbox_received);

#ifdef PBL_HEALTH
  send_health();
  request_sleep();
#endif
}

static void deinit(void) {
#ifdef PBL_HEALTH
  health_service_events_unsubscribe();
  send_health();  // final cumulative snapshot before exit
#endif
  tick_timer_service_unsubscribe();
  battery_state_service_unsubscribe();
  window_destroy(s_window);
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}