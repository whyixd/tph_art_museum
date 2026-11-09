// Moon Observe motor PWM bench test for Seeed Studio XIAO SAMD21.
// Serial Monitor: 115200 baud, Newline (or Both NL & CR).
// Commands: s=start power sequence, x=soft stop and power off,
//           p <0..255>=target PWM duty, +=increase 1, -=decrease 1,
//           ?=status, h=help.
// PWM uses the Arduino core's analogWrite frequency and 3.3 V logic.
// This sketch assumes HIGH-active PWM; verify the driver's input requirements.

#include "board_config.h"
#include <stdlib.h>
#include <string.h>

enum class MotorPowerState : uint8_t {
  POWER_OFF,
  PRECHARGING,
  MAIN_RELAY_SETTLING,
  PRECHARGE_RELEASE,
  MOTOR_READY,
  MOTOR_RUNNING,
  MOTOR_STOPPING,
  POWER_DOWN
};

// Explicit declarations keep Arduino's sketch preprocessor from placing
// prototypes using MotorPowerState before the enum definition.
const char* stateName(MotorPowerState state);
void enterPowerState(MotorPowerState nextState);

MotorPowerState powerState = MotorPowerState::POWER_OFF;
unsigned long stateStartedAt = 0;
unsigned long lastRampAt = 0;
unsigned long pwmZeroAt = 0;
bool pwmZeroAtValid = false;
uint8_t currentDuty = 0;
uint8_t targetDuty = 0;
bool preRelayOn = false;
bool mainRelayOn = false;

constexpr size_t COMMAND_BUFFER_SIZE = 32;
char commandBuffer[COMMAND_BUFFER_SIZE];
size_t commandLength = 0;
bool commandOverflow = false;
bool serialWasConnected = false;

bool pwmConfigured() {
  return MOTOR_PWM_PIN == D1 || MOTOR_PWM_PIN == D4 ||
         MOTOR_PWM_PIN == D5 || MOTOR_PWM_PIN == D6 ||
         MOTOR_PWM_PIN == D7 || MOTOR_PWM_PIN == D8 ||
         MOTOR_PWM_PIN == D9 || MOTOR_PWM_PIN == D10;
}

const char* stateName(MotorPowerState state) {
  switch (state) {
    case MotorPowerState::POWER_OFF: return "POWER_OFF";
    case MotorPowerState::PRECHARGING: return "PRECHARGING";
    case MotorPowerState::MAIN_RELAY_SETTLING: return "MAIN_RELAY_SETTLING";
    case MotorPowerState::PRECHARGE_RELEASE: return "PRECHARGE_RELEASE";
    case MotorPowerState::MOTOR_READY: return "MOTOR_READY";
    case MotorPowerState::MOTOR_RUNNING: return "MOTOR_RUNNING";
    case MotorPowerState::MOTOR_STOPPING: return "MOTOR_STOPPING";
    case MotorPowerState::POWER_DOWN: return "POWER_DOWN";
    default: return "INVALID";
  }
}

void setPreRelay(bool on) {
  digitalWrite(PRE_RELAY_PIN, on ? HIGH : LOW);
  preRelayOn = on;
}

void setMainRelay(bool on) {
  digitalWrite(MAIN_RELAY_PIN, on ? HIGH : LOW);
  mainRelayOn = on;
}

// The single PWM write path enforces the power-sequence interlock.
void setMotorPwm(uint8_t duty) {
  if (powerState != MotorPowerState::MOTOR_RUNNING &&
      powerState != MotorPowerState::MOTOR_STOPPING) {
    duty = 0;
  }
  currentDuty = duty;
  if (pwmConfigured()) {
    analogWrite(MOTOR_PWM_PIN, duty);
  }
}

void enterPowerState(MotorPowerState nextState) {
  const MotorPowerState previousState = powerState;

  switch (nextState) {
    case MotorPowerState::POWER_OFF:
    case MotorPowerState::POWER_DOWN:
      setMotorPwm(0);
      setPreRelay(false);
      setMainRelay(false);
      break;
    case MotorPowerState::PRECHARGING:
      setMotorPwm(0);
      setMainRelay(false);
      setPreRelay(true);
      break;
    case MotorPowerState::MAIN_RELAY_SETTLING:
      setMotorPwm(0);
      setPreRelay(true);
      setMainRelay(true);
      break;
    case MotorPowerState::PRECHARGE_RELEASE:
    case MotorPowerState::MOTOR_READY:
      setMotorPwm(0);
      setPreRelay(false);
      setMainRelay(true);
      break;
    case MotorPowerState::MOTOR_RUNNING:
    case MotorPowerState::MOTOR_STOPPING:
      // The main path must stay energized while PWM ramps.
      setPreRelay(false);
      setMainRelay(true);
      break;
    default:
      setMotorPwm(0);
      setPreRelay(false);
      setMainRelay(false);
      nextState = MotorPowerState::POWER_OFF;
      targetDuty = 0;
      break;
  }

  powerState = nextState;
  stateStartedAt = millis();
  if (nextState == MotorPowerState::MOTOR_READY) {
    lastRampAt = stateStartedAt - PWM_RAMP_INTERVAL_MS;
  }
  if (Serial) {
    Serial.print(F("[MOTOR_PWR] "));
    Serial.print(stateName(previousState));
    Serial.print(F(" -> "));
    Serial.println(stateName(powerState));
  }
}

void motorPowerInit() {
  // Load LOW into output latches before switching the pins to OUTPUT.
  if (pwmConfigured()) {
    digitalWrite(MOTOR_PWM_PIN, LOW);
    pinMode(MOTOR_PWM_PIN, OUTPUT);
    analogWriteResolution(8);
    analogWrite(MOTOR_PWM_PIN, 0);
  }
  digitalWrite(PRE_RELAY_PIN, LOW);
  digitalWrite(MAIN_RELAY_PIN, LOW);
  pinMode(PRE_RELAY_PIN, OUTPUT);
  pinMode(MAIN_RELAY_PIN, OUTPUT);
  preRelayOn = false;
  mainRelayOn = false;
  currentDuty = 0;
  targetDuty = 0;
  powerState = MotorPowerState::POWER_OFF;
  stateStartedAt = millis();
}

void motorPowerStart() {
  if (!pwmConfigured()) {
    Serial.println(F("[ERROR] Set MOTOR_PWM_PIN in board_config.h first."));
    return;
  }
  if (powerState == MotorPowerState::POWER_OFF) {
    enterPowerState(MotorPowerState::PRECHARGING);
  } else {
    Serial.println(F("[INFO] Already on or changing power state."));
  }
}

void motorPowerStop() {
  targetDuty = 0;
  if (powerState == MotorPowerState::POWER_OFF ||
      powerState == MotorPowerState::POWER_DOWN ||
      powerState == MotorPowerState::MOTOR_STOPPING) {
    return;
  }
  if (powerState == MotorPowerState::PRECHARGING ||
      powerState == MotorPowerState::MAIN_RELAY_SETTLING ||
      powerState == MotorPowerState::PRECHARGE_RELEASE) {
    enterPowerState(MotorPowerState::POWER_OFF);
    return;
  }
  const uint8_t stopFromDuty = currentDuty;
  enterPowerState(MotorPowerState::MOTOR_STOPPING);
  lastRampAt = millis();
  pwmZeroAtValid = false;
  Serial.print(F("[PWM] Gradual stop from duty "));
  Serial.println(stopFromDuty);
}

void setTargetDuty(uint8_t duty) {
  if (powerState == MotorPowerState::MOTOR_STOPPING ||
      powerState == MotorPowerState::POWER_DOWN) {
    Serial.println(F("[INFO] Stop in progress; wait for POWER_OFF."));
    return;
  }
  targetDuty = duty;
  Serial.print(F("[PWM] Target duty: "));
  Serial.print(targetDuty);
  Serial.print(F("/255 ("));
  Serial.print((100UL * targetDuty + 127) / 255);
  Serial.println(F("%)"));
  if (powerState == MotorPowerState::POWER_OFF) {
    Serial.println(F("[INFO] Send s to run the power sequence."));
  }
}

uint8_t approachDuty(uint8_t current, uint8_t target, uint8_t step) {
  if (current < target) {
    const uint16_t next = static_cast<uint16_t>(current) + step;
    return next < target ? static_cast<uint8_t>(next) : target;
  }
  if (current > target) {
    const int16_t next = static_cast<int16_t>(current) - step;
    return next > target ? static_cast<uint8_t>(next) : target;
  }
  return current;
}

void updateMotorRamp(unsigned long now) {
  if (now - lastRampAt < PWM_RAMP_INTERVAL_MS) return;
  lastRampAt = now;
  const bool stopping = powerState == MotorPowerState::MOTOR_STOPPING;
  const uint8_t requested = stopping ? 0 : targetDuty;
  const uint8_t step = stopping ? PWM_STOP_RAMP_STEP : PWM_RAMP_STEP;
  setMotorPwm(approachDuty(currentDuty, requested, step));
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
        enterPowerState(MotorPowerState::MOTOR_READY);
      break;
    case MotorPowerState::MOTOR_READY:
      if (targetDuty > 0) {
        enterPowerState(MotorPowerState::MOTOR_RUNNING);
        updateMotorRamp(now);
      }
      break;
    case MotorPowerState::MOTOR_RUNNING:
      updateMotorRamp(now);
      if (currentDuty == 0 && targetDuty == 0)
        enterPowerState(MotorPowerState::MOTOR_READY);
      break;
    case MotorPowerState::MOTOR_STOPPING:
      updateMotorRamp(now);
      if (currentDuty == 0 && !pwmZeroAtValid) {
        pwmZeroAt = now;
        pwmZeroAtValid = true;
        if (Serial) {
          Serial.print(F("[PWM] Duty is 0; waiting "));
          Serial.print(MOTOR_STOP_SETTLE_MS);
          Serial.println(F(" ms before power off"));
        }
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

void printStatus() {
  Serial.print(F("[STATUS] state="));
  Serial.print(stateName(powerState));
  Serial.print(F(" pre="));
  Serial.print(preRelayOn ? F("ON") : F("OFF"));
  Serial.print(F(" main="));
  Serial.print(mainRelayOn ? F("ON") : F("OFF"));
  Serial.print(F(" duty="));
  Serial.print(currentDuty);
  Serial.print(F("/255 target="));
  Serial.println(targetDuty);
}

void printHelp() {
  Serial.println(F("Commands (send newline):"));
  Serial.println(F("  s       start pre-charge and power on"));
  Serial.println(F("  p N     set target duty N=0..255"));
  Serial.println(F("  + / -   change target by 5"));
  Serial.println(F("  x       slow ramp to 0, wait 300 ms, power off"));
  Serial.println(F("  ?       show state and duty"));
  Serial.println(F("  h       show this help"));
}

void handleCommand(char* line) {
  while (*line == ' ' || *line == '\t') ++line;
  char* end = line + strlen(line);
  while (end > line && (end[-1] == ' ' || end[-1] == '\t')) *--end = '\0';

  if (*line == '\0') return;
  if (strcmp(line, "s") == 0) {
    motorPowerStart();
  } else if (strcmp(line, "x") == 0) {
    motorPowerStop();
  } else if (strcmp(line, "?") == 0) {
    printStatus();
  } else if (strcmp(line, "h") == 0) {
    printHelp();
  } else if (strcmp(line, "+") == 0) {
    const uint16_t next = static_cast<uint16_t>(targetDuty) + PWM_ADJUST_STEP;
    setTargetDuty(next > 255 ? 255 : static_cast<uint8_t>(next));
  } else if (strcmp(line, "-") == 0) {
    setTargetDuty(targetDuty > PWM_ADJUST_STEP
                      ? targetDuty - PWM_ADJUST_STEP : 0);
  } else if (line[0] == 'p' && (line[1] == ' ' || line[1] == '\t')) {
    char* number = line + 1;
    while (*number == ' ' || *number == '\t') ++number;
    if (*number < '0' || *number > '9') {
      Serial.println(F("[ERROR] Use p N with N=0..255."));
      return;
    }
    char* afterNumber;
    const long parsed = strtol(number, &afterNumber, 10);
    while (*afterNumber == ' ' || *afterNumber == '\t') ++afterNumber;
    if (*afterNumber != '\0' || parsed > 255) {
      Serial.println(F("[ERROR] Use p N with N=0..255."));
      return;
    }
    setTargetDuty(static_cast<uint8_t>(parsed));
  } else {
    Serial.println(F("[ERROR] Unknown command. Send h for help."));
  }
}

void handleSerialCommands() {
  uint8_t bytesHandled = 0;
  while (Serial.available() > 0 && bytesHandled++ < 32) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\r' || c == '\n') {
      if (commandOverflow) {
        Serial.println(F("[ERROR] Command too long."));
      } else if (commandLength > 0) {
        commandBuffer[commandLength] = '\0';
        handleCommand(commandBuffer);
      }
      commandLength = 0;
      commandOverflow = false;
    } else if (commandLength < COMMAND_BUFFER_SIZE - 1) {
      commandBuffer[commandLength++] = c;
    } else {
      commandOverflow = true;
    }
  }
}

void setup() {
  motorPowerInit();
  Serial.begin(115200);
}

void loop() {
  const bool serialConnected = static_cast<bool>(Serial);
  if (serialConnected && !serialWasConnected) {
    Serial.println(F("Moon Observe motor PWM test"));
    printHelp();
    printStatus();
  }
  serialWasConnected = serialConnected;
  handleSerialCommands();
  motorPowerUpdate();
}
