#pragma once

// XIAO SAMD21 wiring. Confirm these against the actual board before testing.
constexpr uint8_t PRE_RELAY_PIN = D2;
constexpr uint8_t MAIN_RELAY_PIN = D3;

// Set this to the XIAO pin connected to the motor driver's PWM input.
// -1 keeps both relays OFF until the wiring has been confirmed.
// Use a hardware PWM pin (D1 or D4-D10; D2/D3 are already used by relays).
#ifndef MOTOR_PWM_PIN
#define MOTOR_PWM_PIN D1
#endif

// All values below are in one place so bench testing can tune them.
constexpr unsigned long PRECHARGE_TIME_MS = 500;
constexpr unsigned long MAIN_RELAY_SETTLE_MS = 100;
constexpr unsigned long POST_PRECHARGE_OFF_MS = 100;
constexpr unsigned long MOTOR_STOP_SETTLE_MS = 300;
constexpr unsigned long PWM_RAMP_INTERVAL_MS = 80;
constexpr uint8_t PWM_RAMP_STEP = 1;
constexpr uint8_t PWM_STOP_RAMP_STEP = 1;
constexpr uint8_t PWM_ADJUST_STEP = 1;

static_assert(PWM_RAMP_STEP > 0 && PWM_STOP_RAMP_STEP > 0 &&
                  PWM_RAMP_INTERVAL_MS > 0,
              "PWM ramp must make progress");
