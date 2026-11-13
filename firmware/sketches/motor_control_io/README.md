# Moon Observe motor control

Select **Seeeduino XIAO** (SAMD21) in Arduino IDE and open `motor_control_io.ino`. Set `TARGET_PWM_DUTY` in `board_config.h` to the duty established by `motor_pwm_test`. The current configured value is 54, with a compile-time range of 0–90. Zero disables motor start.

## D4 normal run/stop input

The command input is **D4** (`MOTOR_COMMAND_PIN`) with `INPUT_PULLUP`. Connect a dry contact or open-drain output between **D4 and GND**. Open/high requests normal stop; closed/low requests start. For an actively driven input, use a shared ground and a 0–3.3 V signal. Do not connect 5 V, 12 V, or 48 V directly to D4. D4 is also the I2C SDA pin, so do not use `Wire` at the same time.

In production D4 is driven by GL-C-616WL IO13 through the WLED usermod in `../../wled/` (Art-Net channel 469). Wire IO13 through 1 kΩ to D4, add an external 10 kΩ pull-up from D4 to the XIAO 3.3 V so D4 stays HIGH while the ESP32 boots, and connect the two GNDs directly. See the wiring section of `../../wled/README.md` for component placement and values (that document and its drawing still name the pin D8; the parts and values are the same).

The pin is **polled in every loop and acted on by level**, with no interrupt. A level counts once it has held for 30 ms:

- **HIGH** stops the motor, from any state.
- **LOW** starts it when the power is off and a HIGH has been seen since boot or since the last start. A LOW that arrives during the soft stop is kept: the motor starts by itself once the power is off, as long as the pin is still LOW.

At boot, D1 PWM, D2 pre-charge relay, and D3 main relay are initialized OFF. A LOW held through boot does not start the motor; the pin has to go HIGH and then LOW.

On a start, KPRE turns ON for 500 ms; KMAIN turns ON for 100 ms while KPRE remains ON; KPRE turns OFF for 100 ms; then PWM rises by 1 duty count every 120 ms to `TARGET_PWM_DUTY` (about 7.2 seconds in all with a duty of 54). A HIGH command during pre-charge releases the relays immediately because PWM has not started.

On a stop during ramp or running, PWM falls by 1 duty count every 120 ms. KMAIN remains ON until PWM reaches zero and another 300 ms have elapsed, then turns OFF. With the configured duty of 54, a stop from full duty takes about 6.8 seconds including the 30 ms input debounce.

This is a normal controlled stop, not an emergency stop. The 300 ms wait does not confirm that the rotor has stopped; there is no stop feedback sensor. For long or noisy wiring, provide a suitable external pull-up, filtering, or isolation at the input.

Every state change is printed on the USB serial port at 115200 baud as `[MOTOR_PWR] A -> B`. `backend/scripts/motor_cycle_test.py --xiao-port` reads these lines to judge repeated starts and stops automatically.

## Why polling (2026-11-14)

The earlier version set a flag in a `CHANGE` interrupt and read the pin only after the flag was seen. D4 is PA08, the SAMD21 **NMI** pin: the Seeed core attaches it as a non-maskable interrupt, so the `noInterrupts()` around "read the flag, clear the flag" did not protect it, and nothing read the pin again once an edge was lost. On the bench (one unit, 20 start/stop cycles sent through WLED, the WLED output checked each time) one start produced no state change at all; by eye an earlier run showed 3 stops of 20 missed. That version also ignored a LOW received during the soft stop for good, because WLED holds a level and never repeats the edge.
