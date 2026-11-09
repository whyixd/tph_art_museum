// Host-side state machine check. Compile with:
// g++ -std=c++17 -Wall -Wextra -pedantic firmware/tests/motor_pwm_test_host.cpp -o firmware/tests/motor_pwm_test_host.exe

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <sstream>
#include <string>
#include <vector>

#define D0 0
#define D1 1
#define D2 2
#define D3 3
#define D4 4
#define D5 5
#define D6 6
#define D7 7
#define D8 8
#define D9 9
#define D10 10
#define MOTOR_PWM_PIN D1
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define F(text) text

struct PinEvent {
  unsigned long at;
  int pin;
  int value;
  bool pwm;
};

unsigned long fakeMillis = 0;
int pinValues[11] = {};
std::vector<PinEvent> pinEvents;

unsigned long millis() { return fakeMillis; }
void pinMode(int, int) {}
void digitalWrite(int pin, int value) {
  pinValues[pin] = value;
  pinEvents.push_back({fakeMillis, pin, value, false});
}
void analogWriteResolution(int bits) { assert(bits == 8); }
void analogWrite(int pin, int value) {
  // A nonzero PWM pulse must only follow the completed power sequence.
  if (value > 0) {
    assert(pinValues[D3] == HIGH);
    assert(pinValues[D2] == LOW);
    assert(fakeMillis >= 700);
  }
  pinValues[pin] = value;
  pinEvents.push_back({fakeMillis, pin, value, true});
}

struct FakeSerial {
  std::deque<char> input;
  std::ostringstream output;
  explicit operator bool() const { return true; }
  void begin(int baud) { assert(baud == 115200); }
  int available() const { return static_cast<int>(input.size()); }
  int read() {
    const int result = input.front();
    input.pop_front();
    return result;
  }
  template <class T> void print(const T& value) { output << value; }
  template <class T> void println(const T& value) { output << value << '\n'; }
};

FakeSerial Serial;
#include "../sketches/motor_pwm_test/motor_pwm_test.ino"

void send(const char* command) {
  while (*command) Serial.input.push_back(*command++);
  loop();
}

void advance(unsigned long ms) {
  fakeMillis += ms;
  loop();
}

int main() {
  setup();
  assert(powerState == MotorPowerState::POWER_OFF);
  assert(pinValues[D2] == LOW && pinValues[D3] == LOW && pinValues[D1] == 0);

  send("p 100\n");
  assert(targetDuty == 100 && currentDuty == 0);
  send("p 999\n");
  assert(targetDuty == 100);
  send("s\n");
  assert(powerState == MotorPowerState::PRECHARGING);
  assert(pinValues[D2] == HIGH && pinValues[D3] == LOW);
  advance(499);
  assert(powerState == MotorPowerState::PRECHARGING);
  advance(1);
  assert(powerState == MotorPowerState::MAIN_RELAY_SETTLING);
  assert(pinValues[D2] == HIGH && pinValues[D3] == HIGH);
  advance(100);
  assert(powerState == MotorPowerState::PRECHARGE_RELEASE);
  assert(pinValues[D2] == LOW && pinValues[D3] == HIGH);
  advance(100);
  assert(powerState == MotorPowerState::MOTOR_READY && currentDuty == 0);
  advance(0);
  assert(powerState == MotorPowerState::MOTOR_RUNNING && currentDuty == 5);
  advance(20);
  assert(currentDuty == 10);

  send("x\n");
  assert(powerState == MotorPowerState::MOTOR_STOPPING);
  assert(pinValues[D3] == HIGH && currentDuty == 10);
  advance(20);
  assert(currentDuty == 8 && pinValues[D3] == HIGH);
  advance(20);
  assert(currentDuty == 6 && pinValues[D3] == HIGH);
  advance(20);
  assert(currentDuty == 4 && pinValues[D3] == HIGH);
  advance(20);
  assert(currentDuty == 2 && pinValues[D3] == HIGH);
  advance(20);
  assert(currentDuty == 0 && pinValues[D3] == HIGH);
  advance(299);
  assert(powerState == MotorPowerState::MOTOR_STOPPING);
  advance(1);
  assert(powerState == MotorPowerState::POWER_DOWN);
  assert(pinValues[D2] == LOW && pinValues[D3] == LOW);
  advance(0);
  assert(powerState == MotorPowerState::POWER_OFF);

  // p 0 stops the motor without releasing the main relay.
  send("p 30\r\n");
  send("s\n");
  advance(500);
  advance(100);
  advance(100);
  advance(0);
  assert(powerState == MotorPowerState::MOTOR_RUNNING && currentDuty == 5);
  send("p 0\n");
  advance(20);
  assert(powerState == MotorPowerState::MOTOR_READY && currentDuty == 0);
  assert(pinValues[D3] == HIGH && pinValues[D2] == LOW);
  send("x\n");
  advance(300);
  advance(0);
  assert(powerState == MotorPowerState::POWER_OFF);

  send("s\n");
  assert(powerState == MotorPowerState::PRECHARGING);
  send("x\n");
  assert(powerState == MotorPowerState::POWER_OFF);
  assert(pinValues[D2] == LOW && pinValues[D3] == LOW);

  powerState = static_cast<MotorPowerState>(255);
  loop();
  assert(powerState == MotorPowerState::POWER_OFF);
  assert(pinValues[D1] == 0 && pinValues[D2] == LOW && pinValues[D3] == LOW);
}
