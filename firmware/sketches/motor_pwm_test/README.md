# Motor PWM test

Open `motor_pwm_test.ino` with **Seeed Studio XIAO SAMD21** selected. `MOTOR_PWM_PIN` in `board_config.h` is currently set to D1; confirm that the motor driver's PWM input is wired there before connecting the 48 V motor load. Setting it to `-1` makes the `s` command refuse to energize either relay. D2 and D3 are already used by the pre-charge and main relays. D0 is a DAC output, not a hardware PWM pin. Confirm that the motor driver accepts the XIAO's 3.3 V, HIGH-active `analogWrite()` PWM signal and the Arduino core's PWM frequency.

Use Serial Monitor at **115200 baud** with **Newline** or **Both NL & CR**. Commands:

| Command | Action |
|---|---|
| `s` | Start the power sequence. No relay turns on automatically at boot. |
| `p N` | Set target duty from 0 to 255; valid before or after `s`. |
| `+` / `-` | Adjust target duty by 1. |
| `?` | Show state, relay outputs, current duty and target duty. |
| `x` | Lower duty from its current value to 0, wait 300 ms, then release both relays. |
| `h` | Show command help. |

Power-on timing is KPRE ON for 500 ms, then KMAIN ON with KPRE still ON for 100 ms, then KPRE OFF for 100 ms before any nonzero PWM. Normal duty changes ramp in steps of 1 every 80 ms. `x` lowers the current duty in steps of 1 every 80 ms while KMAIN stays ON, then holds duty at 0 for 300 ms before power-off. `p 0` uses the normal ramp and keeps KMAIN on. All timing and ramp values are in `board_config.h`. The 300 ms is a timed settling period; this sketch has no motor-stop feedback sensor.

`../../tests/motor_pwm_test_host.cpp` is a desktop simulation of the sequence, PWM interlock, normal stop, pre-charge abort, and invalid-state fallback. It lives outside the Arduino sketch folder so the Arduino IDE will not compile it as firmware. It does not check the motor driver's required PWM frequency or the physical wiring.
