#include <SPI.h>
#include <RH_RF95.h>

#define RFM95_CS   8
#define RFM95_INT  3
#define RFM95_RST  4
#define RF95_FREQ  868.1

// ====================================================================
// EMITTER E0 — Place at CORNER: bottom-left (x=0, y=0)
// EMITTER_ID = 0  →  TDMA offset = 0 ms
// ====================================================================
const int EMITTER_ID = 0;
const int TX_OFFSET  = EMITTER_ID * 100;   // 0 ms

RH_RF95 rf95(RFM95_CS, RFM95_INT);
uint32_t seq = 0;

void setup() {
    Serial.begin(115200);
    pinMode(RFM95_RST, OUTPUT);
    digitalWrite(RFM95_RST, HIGH);
    digitalWrite(RFM95_RST, LOW);  delay(10);
    digitalWrite(RFM95_RST, HIGH); delay(10);

    if (!rf95.init()) {
        Serial.println("LoRa init failed"); while(1);
    }
    rf95.setFrequency(RF95_FREQ);
    rf95.setModemConfig(RH_RF95::Bw125Cr45Sf128);
    rf95.setTxPower(13, false);
    Serial.println("E0 Ready — EMITTER_ID=0 — corner (0,0)");
}

void loop() {
    uint32_t pos = millis() % 500;
    if (pos >= TX_OFFSET && pos < TX_OFFSET + 50) {
        char payload[32];
        snprintf(payload, sizeof(payload), "UE07,13,%d,%lu", EMITTER_ID, seq++);
        if (rf95.isChannelActive()) delay(random(10, 40));
        rf95.send((uint8_t*)payload, strlen(payload));
        rf95.waitPacketSent();
        Serial.printf("Sent: %s\n", payload);
        delay(400);
    } else {
        delay(5);
    }
}
