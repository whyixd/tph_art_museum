// Moon Observe motor controller for Seeed Studio XIAO SAMD21.
// Command input (MOTOR_COMMAND_PIN, D4): HIGH or disconnected requests stop,
// LOW requests the normal power sequence and PWM ramp. The pin is polled in
// every loop and acted on by level, not by edge: D4 is the SAMD21 NMI pin,
// which noInterrupts() cannot mask, and the earlier interrupt-flag version
// lost edges and then never read the pin again.

#include "board_config.h"

enum class MotorPowerState : uint8_t {
  POWER_OFF,
  PRECHARGING,
  MAIN_RELAY_SETTLING,
  PRECHARGE_RELEASE,
  MOTOR_RAMPING,
  MOTOR_RUNNING,
  MOTOR_STOPPING,
  POWER_DOWN
};

// Explicit prototypes avoid Arduino's auto-prototype placement before the enum.
const char* stateName(MotorPowerState state);
void enterPowerState(MotorPowerState nextState);
void motorPowerStart();
void motorPowerStop();

MotorPowerState powerState = MotorPowerState::POWER_OFF;
unsigned long stateStartedAt = 0;
unsigned long lastRampAt = 0;
unsigned long pwmZeroAt = 0;
bool pwmZeroAtValid = false;
uint8_t currentDuty = 0;
bool commandCandidateHigh = true;
bool commandStableHigh = true;
bool commandStartArmed = false;
unsigned long commandCandidateSince = 0;

void updateMotorCommand() {
  const unsigned long now = millis();
  const bool pinHigh = digitalRead(MOTOR_COMMAND_PIN) == HIGH;

  // Debounce: a level counts once it has held for MOTOR_COMMAND_DEBOUNCE_MS.
  if (pinHigh != commandCandidateHigh) {
    commandCandidateHigh = pinHigh;
    commandCandidateSince = now;
  }
  if (commandStableHigh != commandCandidateHigh &&
      now - commandCandidateSince >= MOTOR_COMMAND_DEBOUNCE_MS) {
    commandStableHigh = commandCandidateHigh;
    // A start needs a HIGH seen since boot or since the last start.
    if (commandStableHigh) commandStartArmed = true;
  }

  // Act on the level. A LOW that arrives during the soft stop is kept and
  // starts the motor once the power is off.
  if (commandStableHigh) {
    motorPowerStop();  // Returns at once when already off or stopping.
  } else if (commandStartArmed && powerState == MotorPowerState::POWER_OFF) {
    commandStartArmed = false;
    motorPowerStart();
  }
}

const char* stateName(MotorPowerState state) {
  switch (state) {
    case MotorPowerState::POWER_OFF: return "POWER_OFF";
    case MotorPowerState::PRECHARGING: return "PRECHARGING";
    case MotorPowerState::MAIN_RELAY_SETTLING: return "MAIN_RELAY_SETTLING";
    case MotorPowerState::PRECHARGE_RELEASE: return "PRECHARGE_RELEASE";
    case MotorPowerState::MOTOR_RAMPING: return "MOTOR_RAMPING";
    case MotorPowerState::MOTOR_RUNNING: return "MOTOR_RUNNING";
    case MotorPowerState::MOTOR_STOPPING: return "MOTOR_STOPPING";
    case MotorPowerState::POWER_DOWN: return "POWER_DOWN";
    default: return "INVALID";
  }
}

void setMotorPwm(uint8_t duty) {
  if (powerState != MotorPowerState::MOTOR_RAMPING &&
      powerState != MotorPowerState::MOTOR_RUNNING &&
      powerState != MotorPowerState::MOTOR_STOPPING) {
    duty = 0;
  }
  currentDuty = duty;
  analogWrite(MOTOR_PWM_PIN, duty);
}

void enterPowerState(MotorPowerState nextState) {
  const MotorPowerState previousState = powerState;

  switch (nextState) {
    case MotorPowerState::POWER_OFF:
    case MotorPowerState::POWER_DOWN:
      setMotorPwm(0);
      digitalWrite(PRE_RELAY_PIN, LOW);
      digitalWrite(MAIN_RELAY_PIN, LOW);
      break;
    case MotorPowerState::PRECHARGING:
      setMotorPwm(0);
      digitalWrite(MAIN_RELAY_PIN, LOW);
      digitalWrite(PRE_RELAY_PIN, HIGH);
      break;
    case MotorPowerState::MAIN_RELAY_SETTLING:
      setMotorPwm(0);
      digitalWrite(PRE_RELAY_PIN, HIGH);
      digitalWrite(MAIN_RELAY_PIN, HIGH);
      break;
    case MotorPowerState::PRECHARGE_RELEASE:
      setMotorPwm(0);
      digitalWrite(PRE_RELAY_PIN, LOW);
      digitalWrite(MAIN_RELAY_PIN, HIGH);
      break;
    case MotorPowerState::MOTOR_RAMPING:
    case MotorPowerState::MOTOR_RUNNING:
    case MotorPowerState::MOTOR_STOPPING:
      digitalWrite(PRE_RELAY_PIN, LOW);
      digitalWrite(MAIN_RELAY_PIN, HIGH);
      break;
    default:
      setMotorPwm(0);
      digitalWrite(PRE_RELAY_PIN, LOW);
      digitalWrite(MAIN_RELAY_PIN, LOW);
      nextState = MotorPowerState::POWER_OFF;
      break;
  }

  powerState = nextState;
  stateStartedAt = millis();
  if (nextState == MotorPowerState::MOTOR_RAMPING ||
      nextState == MotorPowerState::MOTOR_STOPPING) {
    lastRampAt = stateStartedAt;
  }
  if (Serial) {
    Serial.print(F("[MOTOR_PWR] "));
    Serial.print(stateName(previousState));
    Serial.print(F(" -> "));
    Serial.println(stateName(powerState));
  }
}

void motorPowerInit() {
  // Latch LOW before setting each pin to OUTPUT.
  digitalWrite(MOTOR_PWM_PIN, LOW);
  digitalWrite(PRE_RELAY_PIN, LOW);
  digitalWrite(MAIN_RELAY_PIN, LOW);
  pinMode(MOTOR_PWM_PIN, OUTPUT);
  pinMode(PRE_RELAY_PIN, OUTPUT);
  pinMode(MAIN_RELAY_PIN, OUTPUT);
  analogWriteResolution(8);
  analogWrite(MOTOR_PWM_PIN, 0);

  currentDuty = 0;
  powerState = MotorPowerState::POWER_OFF;
  stateStartedAt = millis();
}

void motorPowerStart() {
  if (TARGET_PWM_DUTY == 0 || powerState != MotorPowerState::POWER_OFF) return;
  enterPowerState(MotorPowerState::PRECHARGING);
}

// Normal stop: ramp PWM down before releasing motor power.
void motorPowerStop() {
  if (powerState == MotorPowerState::PRECHARGING ||
      powerState == MotorPowerState::MAIN_RELAY_SETTLING ||
      powerState == MotorPowerState::PRECHARGE_RELEASE) {
    // PWM has never been enabled, so the relays can be released immediately.
    enterPowerState(MotorPowerState::POWER_OFF);
    return;
  }
  if (powerState == MotorPowerState::MOTOR_RAMPING ||
      powerState == MotorPowerState::MOTOR_RUNNING) {
    enterPowerState(MotorPowerState::MOTOR_STOPPING);
    pwmZeroAtValid = false;
  }
  // Off, stopping or powering down: nothing to do. This is called in every
  // loop while the command is HIGH. An invalid state is left to
  // motorPowerUpdate(), which drops straight to POWER_OFF.
}

void motorPowerUpdate() {
  const unsigned long now = millis();
  const unsigned long elapsed = now - stateStartedAt;

  switch (powerState) {
    case MotorPowerState::POWER_OFF:
      break;
    case MotorPowerState::PRECHARGING:
      if (elapsed >= PRECHARGE_TIME_MS)
        enterPowerState(MotorPowerState::MAIN_RELAY_SETTLING);
      break;
    case MotorPowerState::MAIN_RELAY_SETTLING:
      if (elapsed >= MAIN_RELAY_SETTLE_MS)
        enterPowerState(MotorPowerState::PRECHARGE_RELEASE);
      break;
    case MotorPowerState::PRECHARGE_RELEASE:
      if (elapsed >= POST_PRECHARGE_OFF_MS)
        enterPowerState(MotorPowerState::MOTOR_RAMPING);
      break;
    case MotorPowerState::MOTOR_RAMPING:
      if (now - lastRampAt >= PWM_RAMP_INTERVAL_MS) {
        lastRampAt = now;
        const uint16_t next = static_cast<uint16_t>(currentDuty) + PWM_RAMP_STEP;
        setMotorPwm(next < TARGET_PWM_DUTY
                        ? static_cast<uint8_t>(next) : TARGET_PWM_DUTY);
        if (currentDuty == TARGET_PWM_DUTY)
          enterPowerState(MotorPowerState::MOTOR_RUNNING);
      }
      break;
    case MotorPowerState::MOTOR_RUNNING:
      break;
    case MotorPowerState::MOTOR_STOPPING:
      if (currentDuty > 0 && now - lastRampAt >= PWM_RAMP_INTERVAL_MS) {
        lastRampAt = now;
        setMotorPwm(currentDuty > PWM_STOP_RAMP_STEP
                        ? currentDuty - PWM_STOP_RAMP_STEP : 0);
      }
      if (currentDuty == 0 && !pwmZeroAtValid) {
        pwmZeroAt = now;
        pwmZeroAtValid = true;
      }
      if (pwmZeroAtValid && now - pwmZeroAt >= MOTOR_STOP_SETTLE_MS)
        enterPowerState(MotorPowerState::POWER_DOWN);
      break;
    case MotorPowerState::POWER_DOWN:
      enterPowerState(MotorPowerState::POWER_OFF);
      break;
    default:
      enterPowerState(MotorPowerState::POWER_OFF);
      break;
  }
}

void setup() {
  motorPowerInit();
  pinMode(MOTOR_COMMAND_PIN, INPUT_PULLUP);
  commandStableHigh = digitalRead(MOTOR_COMMAND_PIN) == HIGH;
  commandCandidateHigh = commandStableHigh;
  // A LOW already present at boot is not a fresh start command.
  commandStartArmed = commandStableHigh;
  commandCandidateSince = millis();
  Serial.begin(115200);
}

void loop() {
  updateMotorCommand();
  motorPowerUpdate();
}
