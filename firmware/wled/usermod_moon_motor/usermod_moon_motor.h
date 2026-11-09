#pragma once

// Moon Observe motor command relay for the Gledopto GL-C-616WL (WLED v0.15.x, see WLED_VERSION).
// Art-Net channel 469 of the first universe drives IO13 → SAMD21 D8 (motor_control_d8):
// LOW requests run, high impedance (D8 pull-up) requests stop. It bypasses every
// LED/realtime setting. The WLED page adds a RAM-only inhibit and a timed manual run
// that is only allowed while no Art-Net arrives.

#ifndef ARDUINO_ARCH_ESP32
  #error "usermod_moon_motor targets the ESP32 GL-C-616WL"
#endif

#include <mutex>
#include "wled.h"
#include "moon_motor_logic.h"

#define USERMOD_ID_MOON_MOTOR 200  // Kept out of const.h so the core patch stays at e131.cpp + usermods_list.cpp.

class MoonMotorUsermod : public Usermod {
  public:
    MoonMotorUsermod() { instance = this; }

    // Called from the Art-Net receive task by the e131.cpp hook.
    static void onArtnet(unsigned universeOffset, const uint8_t* data, unsigned length) {
      MoonMotorUsermod* self = instance;
      if (self == nullptr || universeOffset != 0) return;
      std::lock_guard<std::mutex> lock(self->mutex);
      self->logic.packet(millis(), data, length > 0xFFFF ? 0xFFFF : length);
    }

    void setup() override {
      std::lock_guard<std::mutex> lock(mutex);
      attachPin();
      initDone = true;
    }

    void loop() override {
      std::lock_guard<std::mutex> lock(mutex);
      applyLocked(millis());
    }

    void onUpdateBegin(bool init) override {
      if (!init) return;  // A failed OTA keeps the lock; reboot to resume.
      std::lock_guard<std::mutex> lock(mutex);
      logic.beginUpdate();
      applyLocked(millis());
    }

    void readFromJsonState(JsonObject& root) override {
      if (!initDone) return;
      JsonObject um = root[FPSTR(_stateKey)];
      if (um.isNull()) return;
      std::lock_guard<std::mutex> lock(mutex);
      const uint32_t now = millis();
      if (um["inhibit"].is<bool>()) logic.setInhibit(um["inhibit"].as<bool>());
      if (um["manual"].is<int>() && um["manual"].as<int>() >= 0) logic.manual(now, um["manual"].as<int>());
      applyLocked(now);
    }

    void addToJsonInfo(JsonObject& root) override {
      std::lock_guard<std::mutex> lock(mutex);
      const uint32_t now = millis();
      const moon_motor::Decision d = applyLocked(now);
      const bool live = logic.artnetLive(now);

      JsonObject user = root["u"];
      if (user.isNull()) user = root.createNestedObject("u");

      JsonArray out = user.createNestedArray("馬達輸出");
      out.add(d.output == moon_motor::Output::Run ? "RUN" : "STOP");
      out.add(String(" · ") + statusText(d));

      JsonArray ch = user.createNestedArray("Art-Net ch469");
      if (logic.hasPacket()) {
        ch.add(logic.value());
        ch.add(String(" · ") + logic.packetAgeMs(now) + " ms 前");
      } else {
        ch.add("—");
      }

      JsonArray inhibitRow = user.createNestedArray("禁止運轉");
      if (logic.inhibited()) {
        inhibitRow.add(String("禁止中 ") + confirmButton("UNLOCK", "TAP AGAIN: UNLOCK", "{moonMotor:{inhibit:false}}"));
      } else {
        inhibitRow.add(String(BUTTON_OPEN) + "onclick=\"requestJson({moonMotor:{inhibit:true}});\">LOCK</button>");
      }

      JsonArray manualRow = user.createNestedArray("手動運轉");
      if (d.reason == moon_motor::Reason::Manual) {
        manualRow.add(String(BUTTON_OPEN) + "onclick=\"requestJson({moonMotor:{manual:0}});\">MOTOR OFF</button>");
      } else if (d.reason == moon_motor::Reason::Updating) {
        manualRow.add("OTA 中無法使用");
      } else if (logic.inhibited()) {
        manualRow.add("禁止中無法使用");
      } else if (live) {
        manualRow.add("Pi 控制中無法使用");
      } else {
        const String label = String("MOTOR ON ") + manualSeconds + "s";
        manualRow.add(confirmButton(label, String("TAP AGAIN: ") + label,
                                    String("{moonMotor:{manual:") + manualSeconds + "}}"));
      }

      JsonObject mm = root.createNestedObject(FPSTR(_stateKey));
      mm["output"] = d.output == moon_motor::Output::Run ? "run" : "stop";
      mm["reason"] = reasonCode(d.reason);
      mm["pin"] = attachedPin;
      mm["inhibit"] = logic.inhibited();
      mm["manualRemainingS"] = (d.manualRemainingMs + 999) / 1000;
      if (logic.hasPacket()) {
        mm["value"] = logic.value();
        mm["ageMs"] = logic.packetAgeMs(now);
      }
    }

    void addToConfig(JsonObject& root) override {
      std::lock_guard<std::mutex> lock(mutex);
      JsonObject top = root.createNestedObject(FPSTR(_name));
      top["pin"] = pin;
      top["timeoutMs"] = logic.timeoutMs();
      top["manualSeconds"] = manualSeconds;
    }

    bool readFromConfig(JsonObject& root) override {
      JsonObject top = root[FPSTR(_name)];
      bool configComplete = !top.isNull();
      int8_t newPin = pin;
      uint32_t timeoutMs = moon_motor::DEFAULT_TIMEOUT_MS;
      uint16_t seconds = moon_motor::DEFAULT_MANUAL_SECONDS;
      configComplete &= getJsonValue(top["pin"], newPin, (int8_t)13);
      configComplete &= getJsonValue(top["timeoutMs"], timeoutMs, moon_motor::DEFAULT_TIMEOUT_MS);
      configComplete &= getJsonValue(top["manualSeconds"], seconds, moon_motor::DEFAULT_MANUAL_SECONDS);

      std::lock_guard<std::mutex> lock(mutex);
      logic.setTimeoutMs(timeoutMs);
      manualSeconds = moon_motor::clampManualSeconds(seconds);
      if (newPin != pin) {
        if (initDone) detachPin();
        pin = newPin;
        if (initDone) attachPin();
      }
      return configComplete;
    }

    void appendConfigData() override {
      oappend(F("addInfo('MoonMotor:pin',1,'接 SAMD21 D8；-1 停用');"));
      oappend(F("addInfo('MoonMotor:timeoutMs',1,'ms（200–10000）；超過即停止');"));
      oappend(F("addInfo('MoonMotor:manualSeconds',1,'秒（10–600）');"));
    }

    uint16_t getId() override { return USERMOD_ID_MOON_MOTOR; }

  private:
    static MoonMotorUsermod* instance;
    static const char _name[];
    static const char _stateKey[];

    std::mutex mutex;  // Serialises the Art-Net task, web task and main loop.
    moon_motor::MotorLogic logic;
    moon_motor::Decision last{moon_motor::Output::Stop, moon_motor::Reason::NoSignal, 0};
    moon_motor::Output applied = moon_motor::Output::Stop;
    int8_t pin = 13;
    int8_t attachedPin = -1;
    bool pinError = false;
    uint16_t manualSeconds = moon_motor::DEFAULT_MANUAL_SECONDS;
    bool initDone = false;

    // WLED's btn-xs is a fixed 42 px square for icons, too small for text. A plain btn sizes to its label.
    static constexpr const char* BUTTON_OPEN =
        "<button class=\"btn\" style=\"width:auto;max-width:100%;margin:2px 0;padding:6px 12px;font-size:14px;line-height:1.2\" ";

    // Two taps instead of confirm(): the WLED iOS app never shows that dialog, so it always cancels.
    // The first tap shows `armedText`; the button disarms by itself after 5 s.
    static String confirmButton(const String& label, const String& armedText, const String& command) {
      return String(BUTTON_OPEN) + "onclick=\"if(this.dataset.armed){requestJson(" + command +
             ")}else{var b=this;b.dataset.armed=1;b.textContent='" + armedText +
             "';setTimeout(function(){delete b.dataset.armed;b.textContent='" + label + "'},5000)}\">" + label +
             "</button>";
    }

    // Caller holds `mutex`.
    moon_motor::Decision applyLocked(uint32_t now) {
      last = logic.update(now);
      if (attachedPin >= 0 && last.output != applied) {
        writePin(last.output);
        applied = last.output;
      }
      return last;
    }

    void writePin(moon_motor::Output output) {
      if (output == moon_motor::Output::Run) {
        digitalWrite(attachedPin, LOW);  // Set the level first so OUTPUT never starts HIGH.
        pinMode(attachedPin, OUTPUT);
      } else {
        pinMode(attachedPin, INPUT);     // High impedance: D8 pull-up reads stop.
      }
    }

    void attachPin() {
      pinError = false;
      if (pin < 0) return;
      if (!PinManager::allocatePin(pin, true, PinOwner::UM_Unspecified)) {
        pinError = true;
        return;
      }
      attachedPin = pin;
      pinMode(attachedPin, INPUT);
      applied = moon_motor::Output::Stop;
    }

    void detachPin() {
      if (attachedPin < 0) return;
      pinMode(attachedPin, INPUT);
      PinManager::deallocatePin(attachedPin, PinOwner::UM_Unspecified);
      attachedPin = -1;
      applied = moon_motor::Output::Stop;
    }

    String statusText(const moon_motor::Decision& d) const {
      if (pinError) return String("IO") + pin + " 已被其他功能佔用";
      if (attachedPin < 0) return "已停用（pin = -1）";
      switch (d.reason) {
        case moon_motor::Reason::NoSignal: return "開機後未收到 Art-Net";
        case moon_motor::Reason::Timeout: return "Art-Net 逾時";
        case moon_motor::Reason::ArtnetStop: return "Art-Net 停止";
        case moon_motor::Reason::ArtnetPending: return "Art-Net 運轉（確認中）";
        case moon_motor::Reason::ArtnetRun: return "Art-Net 運轉";
        case moon_motor::Reason::Manual: return String("手動運轉，剩 ") + (d.manualRemainingMs + 999) / 1000 + " 秒";
        case moon_motor::Reason::Inhibited: return "禁止運轉中";
        case moon_motor::Reason::Updating: return "OTA 中，重開機後恢復";
      }
      return "";
    }

    static const char* reasonCode(moon_motor::Reason reason) {
      switch (reason) {
        case moon_motor::Reason::NoSignal: return "no-signal";
        case moon_motor::Reason::Timeout: return "timeout";
        case moon_motor::Reason::ArtnetStop: return "artnet-stop";
        case moon_motor::Reason::ArtnetPending: return "artnet-pending";
        case moon_motor::Reason::ArtnetRun: return "artnet-run";
        case moon_motor::Reason::Manual: return "manual";
        case moon_motor::Reason::Inhibited: return "inhibited";
        case moon_motor::Reason::Updating: return "updating";
      }
      return "";
    }
};

MoonMotorUsermod* MoonMotorUsermod::instance = nullptr;
const char MoonMotorUsermod::_name[] PROGMEM = "MoonMotor";
const char MoonMotorUsermod::_stateKey[] PROGMEM = "moonMotor";

// Declared in the patched e131.cpp.
void moonMotorOnArtnet(unsigned universeOffset, const uint8_t* data, unsigned length) {
  MoonMotorUsermod::onArtnet(universeOffset, data, length);
}
