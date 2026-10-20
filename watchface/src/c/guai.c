#include <pebble.h>

static Window *s_main_window;
static TextLayer *s_time_layer;
static TextLayer *s_date_layer;

#ifdef PBL_HEALTH
static TextLayer *s_steps_layer;
static TextLayer *s_sleep_layer;
static TextLayer *s_calories_layer;
static TextLayer *s_heart_rate_layer;
static bool s_pending_sleep_request = false;
#endif

// -------------------------------------------------------------------------
// Health -> pkjs sync (spec/integration.md message protocol)
//
// C reads cumulative health counters and sends AppMessages to PebbleKit JS,
// which uploads them to the GUAI server (HTTP runs in the phone's JS layer).
// Decimal metrics go as int32 scaled by 10 to stay integer on the wire.
// -------------------------------------------------------------------------

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

  // Current heart-rate reading, if available.
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
  // Previous night: midnight (start of today) back 24h. Its local date is
  // derived by pkjs from TIMESTAMP (midnight of the night's start date).
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
#endif

static void inbox_received(DictionaryIterator *iter, void *context) {
#ifdef PBL_HEALTH
  Tuple *request = dict_find(iter, MESSAGE_KEY_REQUEST);
  if (request && strcmp(request->value->cstring, "sleep_request") == 0) {
    s_pending_sleep_request = false;
    send_sleep();
  }
#endif
}

// -------------------------------------------------------------------------
// Time + health display
// -------------------------------------------------------------------------

static void update_time(void) {
  time_t temp = time(NULL);
  struct tm *tick_time = localtime(&temp);

  static char s_time_buffer[8];
  strftime(s_time_buffer, sizeof(s_time_buffer),
           clock_is_24h_style() ? "%H:%M" : "%I:%M", tick_time);
  text_layer_set_text(s_time_layer, s_time_buffer);

  static char s_date_buffer[16];
  strftime(s_date_buffer, sizeof(s_date_buffer), "%a %b %d", tick_time);
  text_layer_set_text(s_date_layer, s_date_buffer);
}

#ifdef PBL_HEALTH
static void update_health(void) {
  HealthValue steps = health_service_sum_today(HealthMetricStepCount);
  static char s_steps_buffer[24];
  snprintf(s_steps_buffer, sizeof(s_steps_buffer), "Steps: %d", (int)steps);
  text_layer_set_text(s_steps_layer, s_steps_buffer);

  time_t sleep_start = time_start_of_today() - (6 * SECONDS_PER_HOUR);
  time_t sleep_end = time_start_of_today() + (12 * SECONDS_PER_HOUR);
  HealthValue sleep_secs = health_service_sum(HealthMetricSleepSeconds,
                                              sleep_start, sleep_end);
  static char s_sleep_buffer[24];
  snprintf(s_sleep_buffer, sizeof(s_sleep_buffer), "Sleep: %dh %dm",
           (int)(sleep_secs / SECONDS_PER_HOUR),
           (int)((sleep_secs % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE));
  text_layer_set_text(s_sleep_layer, s_sleep_buffer);

  HealthValue kcal = health_service_sum_today(HealthMetricActiveKCalories);
  static char s_kcal_buffer[24];
  snprintf(s_kcal_buffer, sizeof(s_kcal_buffer), "Active: %d kcal", (int)kcal);
  text_layer_set_text(s_calories_layer, s_kcal_buffer);

  time_t now = time(NULL);
  HealthServiceAccessibilityMask mask =
      health_service_metric_accessible(HealthMetricHeartRateBPM, now, now);
  if (mask & HealthServiceAccessibilityMaskAvailable) {
    HealthValue hr = health_service_peek_current_value(HealthMetricHeartRateBPM);
    static char s_hr_buffer[24];
    snprintf(s_hr_buffer, sizeof(s_hr_buffer), "HR: %d bpm", (int)hr);
    text_layer_set_text(s_heart_rate_layer, s_hr_buffer);
  } else {
    text_layer_set_text(s_heart_rate_layer, "HR: --");
  }
}

static void health_handler(HealthEventType event, void *context) {
  update_health();
  // Push fresh data to pkjs whenever health data changes.
  send_health();
}
#endif

static void tick_handler(struct tm *tick_time, TimeUnits units_changed) {
  update_time();
#ifdef PBL_HEALTH
  update_health();
  send_health();
#endif
}

#ifdef PBL_HEALTH
static TextLayer *create_stat_layer(GRect bounds, int y) {
  TextLayer *layer = text_layer_create(GRect(0, y, bounds.size.w, 18));
  text_layer_set_background_color(layer, GColorClear);
  text_layer_set_text_color(layer, GColorWhite);
  text_layer_set_font(layer, fonts_get_system_font(FONT_KEY_GOTHIC_18));
  text_layer_set_text_alignment(layer, GTextAlignmentCenter);
  return layer;
}
#endif

static void main_window_load(Window *window) {
  Layer *window_layer = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(window_layer);

  s_time_layer = text_layer_create(
      GRect(0, PBL_IF_ROUND_ELSE(38, 26), bounds.size.w, 48));
  text_layer_set_background_color(s_time_layer, GColorClear);
  text_layer_set_text_color(s_time_layer, GColorWhite);
  text_layer_set_font(s_time_layer, fonts_get_system_font(FONT_KEY_BITHAM_42_BOLD));
  text_layer_set_text_alignment(s_time_layer, GTextAlignmentCenter);

  s_date_layer = text_layer_create(
      GRect(0, PBL_IF_ROUND_ELSE(88, 76), bounds.size.w, 20));
  text_layer_set_background_color(s_date_layer, GColorClear);
  text_layer_set_text_color(s_date_layer, GColorWhite);
  text_layer_set_font(s_date_layer, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD));
  text_layer_set_text_alignment(s_date_layer, GTextAlignmentCenter);

  layer_add_child(window_layer, text_layer_get_layer(s_time_layer));
  layer_add_child(window_layer, text_layer_get_layer(s_date_layer));

#ifdef PBL_HEALTH
  const int health_y = PBL_IF_ROUND_ELSE(108, 96);
  s_steps_layer = create_stat_layer(bounds, health_y);
  s_sleep_layer = create_stat_layer(bounds, health_y + 18);
  s_calories_layer = create_stat_layer(bounds, health_y + 36);
  s_heart_rate_layer = create_stat_layer(bounds, health_y + 54);

  layer_add_child(window_layer, text_layer_get_layer(s_steps_layer));
  layer_add_child(window_layer, text_layer_get_layer(s_sleep_layer));
  layer_add_child(window_layer, text_layer_get_layer(s_calories_layer));
  layer_add_child(window_layer, text_layer_get_layer(s_heart_rate_layer));
#endif
}

static void main_window_unload(Window *window) {
  text_layer_destroy(s_time_layer);
  text_layer_destroy(s_date_layer);

#ifdef PBL_HEALTH
  text_layer_destroy(s_steps_layer);
  text_layer_destroy(s_sleep_layer);
  text_layer_destroy(s_calories_layer);
  text_layer_destroy(s_heart_rate_layer);
#endif
}

static void init(void) {
  s_main_window = window_create();
  window_set_background_color(s_main_window, GColorBlack);
  window_set_window_handlers(s_main_window, (WindowHandlers){
    .load = main_window_load,
    .unload = main_window_unload
  });
  window_stack_push(s_main_window, true);

  update_time();
  tick_timer_service_subscribe(MINUTE_UNIT, tick_handler);

#ifdef PBL_HEALTH
  health_service_events_subscribe(health_handler, NULL);
#endif

  // AppMessage plumbing: pkjs handles HTTP; C just pushes data.
  const int inbound_size = 64;
  const int outbound_size = 256;
  app_message_open(inbound_size, outbound_size);
  app_message_register_inbox_received(inbox_received);

  // Push an initial snapshot on launch (pkjs buffers/flushes it).
#ifdef PBL_HEALTH
  send_health();
#endif
}

static void deinit(void) {
#ifdef PBL_HEALTH
  health_service_events_unsubscribe();
  send_health();  // final cumulative snapshot before exit
#endif
  window_destroy(s_main_window);
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}
