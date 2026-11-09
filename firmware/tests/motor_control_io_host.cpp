// Host-side D8 command, timing, and relay/PWM interlock checks.
// g++ -std=c++17 -Wall -Wextra -pedantic firmware/tests/motor_control_io_host.cpp -o firmware/tests/motor_control_io_host.exe
#include <cassert>
#include <cstdint>
#include <sstream>
#define D1 1
#define D2 2
#define D3 3
#define D8 8
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define CHANGE 3
#define F(text) text
#define TARGET_PWM_DUTY 37

unsigned long fakeMillis = 0;
int pinValues[9] = {};
void (*commandInterrupt)() = nullptr;
unsigned long millis() { return fakeMillis; }
void noInterrupts() {}
void interrupts() {}
void pinMode(int, int) {}
int digitalRead(int pin) { return pinValues[pin]; }
void digitalWrite(int pin, int value) { pinValues[pin] = value; }
int digitalPinToInterrupt(int pin) { return pin; }
void attachInterrupt(int pin, void (*handler)(), int mode) {
  assert(pin == D8 && mode == CHANGE);
  commandInterrupt = handler;
}
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
  pinValues[D8] = level;
  assert(commandInterrupt);
  commandInterrupt();
  loop();
}

int main() {
  // A LOW held at boot cannot start until a fresh HIGH-to-LOW command.
  pinValues[D8] = LOW;
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

  powerState = static_cast<MotorPowerState>(255);
  loop();
  assert(powerState == MotorPowerState::POWER_OFF);
  assert(pinValues[D1] == LOW && pinValues[D2] == LOW && pinValues[D3] == LOW);
}
