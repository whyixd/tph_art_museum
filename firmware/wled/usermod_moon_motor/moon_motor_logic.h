#pragma once

// Motor command decision for the GL-C-616WL → SAMD21 D8 relay.
// Pure C++ without Arduino/WLED dependencies so host tests can include it.
// The caller serialises every call (packet task, web task and main loop).

#include <cstdint>

namespace moon_motor {

constexpr uint16_t MOTOR_CHANNEL = 469;  // Art-Net channel, 1-based; matches backend control.MOTOR_CHANNEL.
constexpr uint8_t RUN_VALUE = 255;
constexpr uint8_t RUN_FRAMES = 3;        // Consecutive 255 packets before STOP → RUN (75 ms at 40 FPS).
constexpr uint32_t DEFAULT_TIMEOUT_MS = 1000;
constexpr uint32_t MIN_TIMEOUT_MS = 200;
constexpr uint32_t MAX_TIMEOUT_MS = 10000;
constexpr uint16_t DEFAULT_MANUAL_SECONDS = 60;
constexpr uint16_t MIN_MANUAL_SECONDS = 10;
constexpr uint16_t MAX_MANUAL_SECONDS = 600;

enum class Output : uint8_t { Stop, Run };

enum class Reason : uint8_t {
  NoSignal,       // No Art-Net packet since boot.
  Timeout,        // Art-Net stopped arriving.
  ArtnetStop,     // Channel 469 is not 255.
  ArtnetPending,  // 255 received, waiting for RUN_FRAMES.
  ArtnetRun,
  Manual,
  Inhibited,
  Updating        // OTA started; stays until reboot.
};

enum class ManualResult : uint8_t { Started, Stopped, RejectedInhibited, RejectedArtnet, RejectedUpdating };

struct Decision {
  Output output;
  Reason reason;
  uint32_t manualRemainingMs;
};

inline uint32_t clampTimeoutMs(uint32_t ms) {
  return ms < MIN_TIMEOUT_MS ? MIN_TIMEOUT_MS : (ms > MAX_TIMEOUT_MS ? MAX_TIMEOUT_MS : ms);
}

inline uint16_t clampManualSeconds(uint32_t seconds) {
  return seconds < MIN_MANUAL_SECONDS ? MIN_MANUAL_SECONDS
       : (seconds > MAX_MANUAL_SECONDS ? MAX_MANUAL_SECONDS : static_cast<uint16_t>(seconds));
}

class MotorLogic {
 public:
  explicit MotorLogic(uint32_t timeoutMs = DEFAULT_TIMEOUT_MS) : timeoutMs_(clampTimeoutMs(timeoutMs)) {}

  void setTimeoutMs(uint32_t ms) { timeoutMs_ = clampTimeoutMs(ms); }
  uint32_t timeoutMs() const { return timeoutMs_; }

  // One Art-Net packet for the first universe; ignored when it is too short.
  void packet(uint32_t now, const uint8_t* data, uint16_t length) {
    if (data == nullptr || length < MOTOR_CHANNEL) return;
    const uint8_t value = data[MOTOR_CHANNEL - 1];
    if (!live(now) || value != RUN_VALUE) run255_ = 0;
    if (value == RUN_VALUE && run255_ < RUN_FRAMES) run255_++;
    value_ = value;
    lastPacketMs_ = now;
    hasPacket_ = true;
    manualActive_ = false;  // Any Art-Net packet hands control back to the Pi.
  }

  void setInhibit(bool on) {
    inhibit_ = on;
    if (on) manualActive_ = false;
  }

  // seconds == 0 ends a manual run; other values are clamped to the allowed range.
  ManualResult manual(uint32_t now, uint32_t seconds) {
    if (seconds == 0) {
      manualActive_ = false;
      return ManualResult::Stopped;
    }
    if (updating_) return ManualResult::RejectedUpdating;
    if (inhibit_) return ManualResult::RejectedInhibited;
    if (live(now)) return ManualResult::RejectedArtnet;
    manualActive_ = true;
    manualEndMs_ = now + static_cast<uint32_t>(clampManualSeconds(seconds)) * 1000u;
    return ManualResult::Started;
  }

  void beginUpdate() {
    updating_ = true;
    manualActive_ = false;
  }

  Decision update(uint32_t now) {
    if (manualActive_ && elapsed(now, manualEndMs_)) manualActive_ = false;
    Decision d{Output::Stop, Reason::NoSignal, 0};
    if (updating_) {
      d.reason = Reason::Updating;
    } else if (inhibit_) {
      d.reason = Reason::Inhibited;
    } else if (live(now)) {
      if (value_ != RUN_VALUE) {
        d.reason = Reason::ArtnetStop;
      } else if (output_ == Output::Run || run255_ >= RUN_FRAMES) {
        // Already running (manual run or released inhibit): keep running without a gap.
        d.output = Output::Run;
        d.reason = Reason::ArtnetRun;
      } else {
        d.reason = Reason::ArtnetPending;
      }
    } else if (manualActive_) {
      d.output = Output::Run;
      d.reason = Reason::Manual;
      d.manualRemainingMs = manualEndMs_ - now;
    } else if (hasPacket_) {
      d.reason = Reason::Timeout;
    }
    output_ = d.output;
    return d;
  }

  bool inhibited() const { return inhibit_; }
  bool hasPacket() const { return hasPacket_; }
  uint8_t value() const { return value_; }
  // Milliseconds since the last packet; 0 when a packet raced ahead of `now`.
  uint32_t packetAgeMs(uint32_t now) const {
    const int32_t age = static_cast<int32_t>(now - lastPacketMs_);
    return age > 0 ? static_cast<uint32_t>(age) : 0;
  }
  bool artnetLive(uint32_t now) const { return live(now); }

 private:
  // Signed differences keep millis() wrap-around and packets stamped after `now` correct.
  static bool elapsed(uint32_t now, uint32_t deadline) {
    return static_cast<int32_t>(now - deadline) >= 0;
  }
  bool live(uint32_t now) const {
    return hasPacket_ && static_cast<int32_t>(now - lastPacketMs_) <= static_cast<int32_t>(timeoutMs_);
  }

  uint32_t timeoutMs_;
  bool hasPacket_ = false;
  uint32_t lastPacketMs_ = 0;
  uint8_t value_ = 0;
  uint8_t run255_ = 0;
  bool inhibit_ = false;  // RAM only: every boot starts uninhibited.
  bool manualActive_ = false;
  uint32_t manualEndMs_ = 0;
  bool updating_ = false;
  Output output_ = Output::Stop;
};

}  // namespace moon_motor
