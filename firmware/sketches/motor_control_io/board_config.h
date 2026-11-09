#pragma once

// Seeed Studio XIAO SAMD21 wiring confirmed by the relay/PWM test sketches.
constexpr uint8_t PRE_RELAY_PIN = D2;
constexpr uint8_t MAIN_RELAY_PIN = D3;
constexpr uint8_t MOTOR_PWM_PIN = D1;
constexpr uint8_t MOTOR_COMMAND_PIN = D8;
constexpr unsigned long MOTOR_COMMAND_DEBOUNCE_MS = 30;

// Set this to the duty found with motor_pwm_test (1..255).
// Zero keeps the power sequence disabled until the tested value is entered.
#ifndef TARGET_PWM_DUTY
#define TARGET_PWM_DUTY 82
#endif

static_assert(TARGET_PWM_DUTY >= 0 && TARGET_PWM_DUTY <= 90,
              "TARGET_PWM_DUTY must be between 0 and 90");

constexpr unsigned long PRECHARGE_TIME_MS = 500;
constexpr unsigned long MAIN_RELAY_SETTLE_MS = 100;
constexpr unsigned long POST_PRECHARGE_OFF_MS = 100;
constexpr unsigned long PWM_RAMP_INTERVAL_MS = 120;
constexpr uint8_t PWM_RAMP_STEP = 1;
constexpr uint8_t PWM_STOP_RAMP_STEP = 1;
constexpr unsigned long MOTOR_STOP_SETTLE_MS = 300;

static_assert(MOTOR_PWM_PIN != PRE_RELAY_PIN &&
                  MOTOR_PWM_PIN != MAIN_RELAY_PIN,
              "PWM and relay pins must be different");
static_assert(MOTOR_COMMAND_PIN != MOTOR_PWM_PIN &&
                  MOTOR_COMMAND_PIN != PRE_RELAY_PIN &&
                  MOTOR_COMMAND_PIN != MAIN_RELAY_PIN,
              "Command input and motor outputs must be different");
static_assert(PWM_RAMP_INTERVAL_MS > 0 && PWM_RAMP_STEP > 0 &&
                  PWM_STOP_RAMP_STEP > 0,
              "PWM ramp must make progress");
