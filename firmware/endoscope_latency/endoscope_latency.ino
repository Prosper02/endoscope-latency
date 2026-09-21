// Endoscope display-latency probe (both edges)
// Two TEMT6000 light sensors on the screens, white LED in the endoscope's field of view.
//
//   A0  <- SIG of TEMT6000 taped to the LOCAL (mentee) screen
//   A1  <- SIG of TEMT6000 taped to the MENTOR screen
//   A2  -> white LED (+ series resistor to GND), placed in the endoscope's field of view
//
// Each trial:
//   1. LED off for DARK_MS, measure the dark level of each sensor            (base)
//   2. LED on  -> time until each screen rises above  base + threshold        (rise, ON edge)
//   3. LED stays on for LIT_MS so the camera exposure settles, measure level   (lit)
//   4. LED off -> time until each screen falls below (lit + base) / 2         (fall, OFF edge)
// A fall is only attempted on a channel whose lit level is at least MIN_SWING above base.
//
// Prints one CSV line per trial at 115200 baud (nan = not detected within 5 s):
//   trial,baseA,baseB,litA,litB,riseA_ms,riseB_ms,fallA_ms,fallB_ms,linkRise_ms,linkFall_ms
// riseA_ms / riseB_ms = LED-on  to mentee / mentor screen bright  (glass-to-glass)
// fallA_ms / fallB_ms = LED-off to mentee / mentor screen dark
// linkRise_ms = riseB - riseA, linkFall_ms = fallB - fallA = delay added by the telementoring link
//
// Serial commands (115200 baud):
//   r      raw mode, LED off: streams "raw,A0,A1" 10x/s for positioning the sensors
//   l      raw mode, LED held on: use it to find the LED's spot in the video and slide the sensor onto it
//   m      back to measurement mode
//   t<n>   set detection threshold to n ADC counts above baseline, e.g. t80
//   a / b / x   measure only sensor A (A0), only sensor B (A1), or both (default)

const int LED_PIN    = A2;   // A2 used as a digital output
const int MENTEE_PIN = A0;
const int MENTOR_PIN = A1;

int threshold = 30;                           // ADC counts above dark level (0..1023). 30 suits a black endoscope view; 80+ if desktop content is under a sensor
const int CONFIRM = 3;                        // consecutive samples past the level to count as detected
const int MIN_SWING = 4;                      // minimum lit-minus-dark swing (ADC counts) to attempt the OFF edge
const unsigned long DARK_MS    = 2500;        // LED off time so both screens settle to dark
const unsigned long LIT_MS     = 2500;        // LED on time so both screens / camera exposure settle
const unsigned long TIMEOUT_US = 5000000UL;   // give up on an edge after 5 s

bool rawMode = false;
bool useA = true, useB = true;                // which channels a trial waits on
unsigned long trial = 0;

int readAvg(int pin, int n) {
  long sum = 0;
  for (int i = 0; i < n; i++) sum += analogRead(pin);
  return (int)(sum / n);
}

void printHeader() {
  Serial.println(F("trial,baseA,baseB,litA,litB,riseA_ms,riseB_ms,fallA_ms,fallB_ms,linkRise_ms,linkFall_ms"));
}

void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'r' || c == 'l') {
      rawMode = true;
      digitalWrite(LED_PIN, c == 'l' ? HIGH : LOW);
      Serial.print(F("# raw mode, LED ")); Serial.print(c == 'l' ? F("ON") : F("off"));
      Serial.println(F(": raw,A0,A1  (send m to measure)"));
    } else if (c == 'm') {
      rawMode = false;
      Serial.println(F("# measure mode"));
      printHeader();
    } else if (c == 't') {
      int v = Serial.parseInt();
      if (v > 0) threshold = v;
      Serial.print(F("# threshold=")); Serial.println(threshold);
    } else if (c == 'a' || c == 'b' || c == 'x') {
      useA = (c != 'b'); useB = (c != 'a');
      Serial.print(F("# channels: A=")); Serial.print(useA); Serial.print(F(" B=")); Serial.println(useB);
    }
  }
}

// Poll both sensors until each enabled channel crosses its level for CONFIRM samples.
// dir > 0: rising (reading > level); dir < 0: falling (reading < level).
// tA/tB receive the micros() of the first confirming sample, or 0 if not detected.
void waitEdge(bool enA, int levelA, bool enB, int levelB, int dir, unsigned long t0,
              unsigned long &tA, unsigned long &tB) {
  tA = 0; tB = 0;
  unsigned long firstA = 0, firstB = 0;
  int cntA = 0, cntB = 0;
  while (((enA && tA == 0) || (enB && tB == 0)) && (micros() - t0) < TIMEOUT_US) {
    if (Serial.available()) break;            // a typed command abandons the trial
    int a = analogRead(MENTEE_PIN);
    unsigned long ta = micros();
    int b = analogRead(MENTOR_PIN);
    unsigned long tb = micros();
    bool hitA = (dir > 0) ? (a > levelA) : (a < levelA);
    bool hitB = (dir > 0) ? (b > levelB) : (b < levelB);
    if (enA && tA == 0) {
      if (hitA) { if (cntA == 0) firstA = ta; if (++cntA >= CONFIRM) tA = firstA; }
      else cntA = 0;
    }
    if (enB && tB == 0) {
      if (hitB) { if (cntB == 0) firstB = tb; if (++cntB >= CONFIRM) tB = firstB; }
      else cntB = 0;
    }
  }
}

void printMs(unsigned long t, unsigned long t0) {
  if (t) Serial.print((t - t0) / 1000.0, 2); else Serial.print(F("nan"));
}
void printDiff(unsigned long tb, unsigned long ta) {
  if (ta && tb) Serial.print(((long)(tb - ta)) / 1000.0, 2); else Serial.print(F("nan"));
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.begin(115200);
  Serial.setTimeout(50);
  Serial.println(F("# endoscope latency probe, both edges  (A0=mentee, A1=mentor, A2=LED)"));
  Serial.println(F("# commands: r=raw (LED off), l=raw (LED on), m=measure, t<n>=threshold, a/b/x=channel A/B/both"));
  Serial.print(F("# threshold=")); Serial.println(threshold);
  printHeader();
}

void loop() {
  handleSerial();

  if (rawMode) {
    Serial.print(F("raw,"));
    Serial.print(analogRead(MENTEE_PIN)); Serial.print(',');
    Serial.println(analogRead(MENTOR_PIN));
    delay(100);
    return;
  }

  // 1. dark phase
  digitalWrite(LED_PIN, LOW);
  delay(DARK_MS);
  int baseA = readAvg(MENTEE_PIN, 8);
  int baseB = readAvg(MENTOR_PIN, 8);

  // 2. ON edge
  unsigned long t0 = micros();
  digitalWrite(LED_PIN, HIGH);
  unsigned long riseA, riseB;
  waitEdge(useA, baseA + threshold, useB, baseB + threshold, +1, t0, riseA, riseB);
  if (Serial.available()) return;

  // 3. lit phase
  delay(LIT_MS);
  int litA = readAvg(MENTEE_PIN, 8);
  int litB = readAvg(MENTOR_PIN, 8);
  bool okA = useA && (litA - baseA) >= MIN_SWING;
  bool okB = useB && (litB - baseB) >= MIN_SWING;

  // 4. OFF edge
  unsigned long t1 = micros();
  digitalWrite(LED_PIN, LOW);
  unsigned long fallA, fallB;
  waitEdge(okA, (litA + baseA) / 2, okB, (litB + baseB) / 2, -1, t1, fallA, fallB);
  if (Serial.available()) return;

  // report
  trial++;
  Serial.print(trial); Serial.print(',');
  Serial.print(baseA); Serial.print(',');
  Serial.print(baseB); Serial.print(',');
  Serial.print(litA);  Serial.print(',');
  Serial.print(litB);  Serial.print(',');
  printMs(riseA, t0);  Serial.print(',');
  printMs(riseB, t0);  Serial.print(',');
  printMs(fallA, t1);  Serial.print(',');
  printMs(fallB, t1);  Serial.print(',');
  printDiff(riseB, riseA); Serial.print(',');
  printDiff(fallB, fallA); Serial.println();
}
