// Host-side D4 command, timing, and relay/PWM interlock checks.
// The sketch polls the command pin: this harness has no interrupt at all.
// g++ -std=c++17 -Wall -Wextra -pedantic firmware/tests/motor_control_io_host.cpp -o firmware/tests/motor_control_io_host.exe
#include <cassert>
#include <cstdint>
#include <sstream>
#define D1 1
#define D2 2
#define D3 3
#define D4 4
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define F(text) text
#define TARGET_PWM_DUTY 37

unsigned long fakeMillis = 0;
int pinValues[9] = {};
unsigned long millis() { return fakeMillis; }
void pinMode(int, int) {}
int digitalRead(int pin) { return pinValues[pin]; }
void digitalWrite(int pin, int value) { pinValues[pin] = value; }
void analogWriteResolution(int bits) { assert(bits == 8); }
void analogWrite(int pin, int duty) {
  if (duty > 0) {
    assert(pinValues[D2] == LOW && pinValues[D3] == HIGH);
  }
  pinValues[pin] = duty;
}
struct FakeSerial {
  std::ostringstream output;
  explicit operator bool() const { return true; }
  void begin(int baud) { assert(baud == 115200); }
  template <class T> void print(const T& value) { output << value; }
  template <class T> void println(const T& value) { output << value << '\n'; }
};
FakeSerial Serial;
#include "../sketches/motor_control_io/motor_control_io.ino"

void advance(unsigned long ms) { fakeMillis += ms; loop(); }
void commandLevel(int level) {
  pinValues[MOTOR_COMMAND_PIN] = level;
  loop();
}
// From POWER_OFF with the command LOW and accepted: run up to full duty.
void runUp() {
  assert(powerState == MotorPowerState::PRECHARGING);
  advance(500);
  advance(100);
  advance(100);
  for (int i = 0; i < TARGET_PWM_DUTY; ++i) advance(120);
  assert(powerState == MotorPowerState::MOTOR_RUNNING);
}
// From MOTOR_STOPPING at `duty`: ramp down and release the power.
void runDown(int duty) {
  assert(powerState == MotorPowerState::MOTOR_STOPPING);
  for (int i = 0; i < duty; ++i) advance(120);
  advance(300);
  assert(powerState == MotorPowerState::POWER_DOWN);
  advance(0);
  assert(powerState == MotorPowerState::POWER_OFF);
}

int main() {
  assert(MOTOR_COMMAND_PIN == D4);
  // A LOW held at boot cannot start until a fresh HIGH-to-LOW command.
  pinValues[D4] = LOW;
  setup();
  advance(100);
  assert(powerState == MotorPowerState::POWER_OFF);
  commandLevel(HIGH);
  advance(30);
  commandLevel(LOW);
  advance(29);
  assert(powerState == MotorPowerState::POWER_OFF);
  commandLevel(HIGH); // Reject a short pulse.
  advance(30);
  assert(powerState == MotorPowerState::POWER_OFF);

  commandLevel(LOW);
  advance(30);
  assert(powerState == MotorPowerState::PRECHARGING);
  assert(pinValues[D1] == LOW && pinValues[D2] == HIGH && pinValues[D3] == LOW);
  advance(500);
  assert(powerState == MotorPowerState::MAIN_RELAY_SETTLING);
  advance(100);
  assert(powerState == MotorPowerState::PRECHARGE_RELEASE);
  advance(100);
  assert(powerState == MotorPowerState::MOTOR_RAMPING && currentDuty == 0);
  advance(119);
  assert(currentDuty == 0);
  advance(1);
  assert(currentDuty == 1);
  for (int i = 1; i < 37; ++i) advance(120);
  assert(currentDuty == 37 && powerState == MotorPowerState::MOTOR_RUNNING);

  commandLevel(HIGH);
  advance(30);
  assert(powerState == MotorPowerState::MOTOR_STOPPING);
  for (int expected = 36; expected >= 0; --expected) {
    advance(120);
    assert(currentDuty == expected && pinValues[D3] == HIGH);
  }
  advance(299);
  assert(powerState == MotorPowerState::MOTOR_STOPPING);
  advance(1);
  assert(powerState == MotorPowerState::POWER_DOWN);
  advance(0);
  assert(powerState == MotorPowerState::POWER_OFF);
  assert(pinValues[D1] == LOW && pinValues[D2] == LOW && pinValues[D3] == LOW);

  commandLevel(LOW);
  advance(30);
  assert(powerState == MotorPowerState::PRECHARGING);
  commandLevel(HIGH);
  advance(30);
  assert(powerState == MotorPowerState::POWER_OFF);

  // A HIGH shorter than the debounce does not stop a running motor.
  commandLevel(LOW);
  advance(30);
  runUp();
  commandLevel(HIGH);
  advance(29);
  commandLevel(LOW);
  advance(100);
  assert(powerState == MotorPowerState::MOTOR_RUNNING);

  // A LOW that arrives during the soft stop is kept: the motor starts again
  // by itself once the power is off, without another HIGH-to-LOW.
  commandLevel(HIGH);
  advance(30);
  assert(powerState == MotorPowerState::MOTOR_STOPPING);
  for (int i = 0; i < 10; ++i) advance(120);
  commandLevel(LOW);
  advance(30);
  assert(powerState == MotorPowerState::MOTOR_STOPPING);
  runDown(TARGET_PWM_DUTY - 10);
  advance(0);
  assert(powerState == MotorPowerState::PRECHARGING);
  runUp();

  // A LOW during the soft stop that goes HIGH again before the power is off
  // does not start the motor.
  commandLevel(HIGH);
  advance(30);
  for (int i = 0; i < 5; ++i) advance(120);
  commandLevel(LOW);
  advance(30);
  commandLevel(HIGH);
  advance(30);
  runDown(TARGET_PWM_DUTY - 5);
  advance(1000);
  assert(powerState == MotorPowerState::POWER_OFF);

  // The pin is read in every loop: a change with no interrupt at all, even
  // one first seen long after it happened, is acted on.
  pinValues[D4] = LOW;
  advance(5000);
  advance(30);
  runUp();
  pinValues[D4] = HIGH;
  advance(5000);
  advance(30);
  assert(powerState == MotorPowerState::MOTOR_STOPPING);
  runDown(TARGET_PWM_DUTY);

  powerState = static_cast<MotorPowerState>(255);
  loop();
  assert(powerState == MotorPowerState::POWER_OFF);
  assert(pinValues[D1] == LOW && pinValues[D2] == LOW && pinValues[D3] == LOW);
}
