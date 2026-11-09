// Host-side checks for the WLED moon motor relay decision (Art-Net ch469 → IO13).
// g++ -std=c++17 -Wall -Wextra -pedantic firmware/tests/moon_motor_logic_host.cpp -o firmware/tests/moon_motor_logic_host.exe
#include <cassert>
#include <cstdint>
#include "../wled/usermod_moon_motor/moon_motor_logic.h"

using namespace moon_motor;

uint8_t frame[470] = {};

void send(MotorLogic& logic, uint32_t now, uint8_t value, uint16_t length = sizeof(frame)) {
  frame[MOTOR_CHANNEL - 1] = value;
  logic.packet(now, frame, length);
}

void expect(MotorLogic& logic, uint32_t now, Output output, Reason reason) {
  const Decision d = logic.update(now);
  assert(d.output == output);
  assert(d.reason == reason);
}

int main() {
  // Boot is stopped; a short packet does not count as Art-Net.
  {
    MotorLogic logic;
    expect(logic, 0, Output::Stop, Reason::NoSignal);
    send(logic, 10, RUN_VALUE, MOTOR_CHANNEL - 1);
    assert(!logic.hasPacket());
    expect(logic, 10, Output::Stop, Reason::NoSignal);
  }

  // Start needs RUN_FRAMES consecutive 255; any other value stops at once.
  {
    MotorLogic logic;
    send(logic, 0, RUN_VALUE);
    send(logic, 25, RUN_VALUE);
    expect(logic, 25, Output::Stop, Reason::ArtnetPending);
    send(logic, 50, RUN_VALUE);
    expect(logic, 50, Output::Run, Reason::ArtnetRun);
    send(logic, 75, 128);
    expect(logic, 75, Output::Stop, Reason::ArtnetStop);
    send(logic, 100, RUN_VALUE);
    expect(logic, 100, Output::Stop, Reason::ArtnetPending);
    send(logic, 125, 0);
    expect(logic, 125, Output::Stop, Reason::ArtnetStop);
  }

  // Losing Art-Net for longer than the timeout stops; the 255 count restarts.
  {
    MotorLogic logic;
    for (uint32_t t = 0; t <= 50; t += 25) send(logic, t, RUN_VALUE);
    expect(logic, 50, Output::Run, Reason::ArtnetRun);
    expect(logic, 50 + DEFAULT_TIMEOUT_MS, Output::Run, Reason::ArtnetRun);
    expect(logic, 51 + DEFAULT_TIMEOUT_MS, Output::Stop, Reason::Timeout);
    send(logic, 2000, RUN_VALUE);
    expect(logic, 2000, Output::Stop, Reason::ArtnetPending);
  }

  // Manual run only without Art-Net, is clamped, expires and can be stopped.
  {
    MotorLogic logic;
    assert(logic.manual(0, 5) == ManualResult::Started);
    Decision d = logic.update(0);
    assert(d.output == Output::Run && d.reason == Reason::Manual);
    assert(d.manualRemainingMs == MIN_MANUAL_SECONDS * 1000u);
    expect(logic, MIN_MANUAL_SECONDS * 1000u - 1, Output::Run, Reason::Manual);
    expect(logic, MIN_MANUAL_SECONDS * 1000u, Output::Stop, Reason::NoSignal);

    assert(logic.manual(20000, 9999) == ManualResult::Started);
    d = logic.update(20000);
    assert(d.manualRemainingMs == MAX_MANUAL_SECONDS * 1000u);
    assert(logic.manual(21000, 0) == ManualResult::Stopped);
    expect(logic, 21000, Output::Stop, Reason::NoSignal);

    send(logic, 30000, 0);
    assert(logic.manual(30500, 60) == ManualResult::RejectedArtnet);
    expect(logic, 30500, Output::Stop, Reason::ArtnetStop);
    assert(logic.manual(31001, 60) == ManualResult::Started);
    expect(logic, 31001, Output::Run, Reason::Manual);
  }

  // Art-Net ends a manual run: 255 keeps running without a gap, 0 stops.
  {
    MotorLogic logic;
    logic.manual(0, 60);
    expect(logic, 0, Output::Run, Reason::Manual);
    send(logic, 1000, RUN_VALUE);
    expect(logic, 1000, Output::Run, Reason::ArtnetRun);
    expect(logic, 2500, Output::Stop, Reason::Timeout);  // The manual run does not come back.

    MotorLogic other;
    other.manual(0, 60);
    expect(other, 0, Output::Run, Reason::Manual);
    send(other, 1000, 0);
    expect(other, 1000, Output::Stop, Reason::ArtnetStop);
  }

  // Inhibit overrides Art-Net and manual; releasing it while the Pi sends 255 runs at once.
  {
    MotorLogic logic;
    for (uint32_t t = 0; t <= 50; t += 25) send(logic, t, RUN_VALUE);
    expect(logic, 50, Output::Run, Reason::ArtnetRun);
    logic.setInhibit(true);
    expect(logic, 50, Output::Stop, Reason::Inhibited);
    send(logic, 75, RUN_VALUE);
    expect(logic, 75, Output::Stop, Reason::Inhibited);
    logic.setInhibit(false);
    expect(logic, 75, Output::Run, Reason::ArtnetRun);

    MotorLogic manual;
    manual.manual(0, 60);
    manual.setInhibit(true);
    assert(manual.manual(0, 60) == ManualResult::RejectedInhibited);
    manual.setInhibit(false);
    expect(manual, 0, Output::Stop, Reason::NoSignal);  // Inhibit cancelled the manual run.
  }

  // OTA locks STOP until reboot.
  {
    MotorLogic logic;
    logic.manual(0, 60);
    logic.beginUpdate();
    expect(logic, 0, Output::Stop, Reason::Updating);
    assert(logic.manual(0, 60) == ManualResult::RejectedUpdating);
    for (uint32_t t = 0; t <= 100; t += 25) send(logic, t, RUN_VALUE);
    logic.setInhibit(false);
    expect(logic, 100, Output::Stop, Reason::Updating);
  }

  // millis() wrap-around and a packet stamped after the loop's `now`.
  {
    MotorLogic logic;
    const uint32_t nearWrap = 0xFFFFFF00u;
    for (uint32_t i = 0; i < RUN_FRAMES; i++) send(logic, nearWrap + i * 25, RUN_VALUE);
    expect(logic, nearWrap + 600, Output::Run, Reason::ArtnetRun);  // Wrapped past zero.
    assert(logic.packetAgeMs(nearWrap + 600) == 550);
    expect(logic, nearWrap + 50 + DEFAULT_TIMEOUT_MS + 1, Output::Stop, Reason::Timeout);

    MotorLogic race;
    send(race, 1000, 0);
    assert(race.packetAgeMs(990) == 0);
    expect(race, 990, Output::Stop, Reason::ArtnetStop);
  }

  // Timeout is clamped to its safe range.
  {
    MotorLogic logic(0);
    assert(logic.timeoutMs() == MIN_TIMEOUT_MS);
    logic.setTimeoutMs(60000);
    assert(logic.timeoutMs() == MAX_TIMEOUT_MS);
  }
  return 0;
}
