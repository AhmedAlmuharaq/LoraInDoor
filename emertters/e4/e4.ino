#include <SPI.h>
#include <RH_RF95.h>

// Pin definitions for Adafruit Feather M0 / ESP32 with RFM95W
#define RFM95_CS   8
#define RFM95_INT  3
#define RFM95_RST  4
#define RF95_FREQ  868.1

// ====================================================================
// DEPLOYMENT CONFIGURATION
// Change ONLY this value for each board you flash: 0, 1, 2, or 3.
// The Node acts as a blind beacon. X/Y coordinates are managed by Web UI.
// ====================================================================
const int EMITTER_ID = 3; 

// Dynamic Time Division Multiple Access (TDMA) offset based on Node ID
// E0: 0ms, E1: 100ms, E2: 200ms, E3: 300ms
const int TX_OFFSET = EMITTER_ID * 100;

RH_RF95 rf95(RFM95_CS, RFM95_INT);
uint32_t seq = 0;

void setup() {
    Serial.begin(115200);
    
    // Hardware reset for the LoRa module
    pinMode(RFM95_RST, OUTPUT);
    digitalWrite(RFM95_RST, HIGH);
    digitalWrite(RFM95_RST, LOW);
    delay(10);
    digitalWrite(RFM95_RST, HIGH);
    delay(10);
    
    if (!rf95.init()) { 
        Serial.println("LoRa initialization failed. Check wiring."); 
        while(1);
    }
    
    // Configure frequency, bandwidth, spreading factor, and TX power
    rf95.setFrequency(RF95_FREQ);
    rf95.setModemConfig(RH_RF95::Bw125Cr45Sf128); // SF7, BW 125 kHz
    rf95.setTxPower(13, false);                   // +13 dBm

    Serial.printf("Beacon %d Ready. Environment Independent Mode. TX Offset: %d ms.\n", EMITTER_ID, TX_OFFSET);
    
    // Initialize random seed for Carrier-Sense collision avoidance
    randomSeed(analogRead(0));
}

void loop() {
    uint32_t current_time = millis();
    uint32_t window_pos = current_time % 500; // 500 ms global cycle (2Hz transmission)
    
    // Check if it is this node's turn to transmit
    if (window_pos >= TX_OFFSET && window_pos < TX_OFFSET + 50) {
        char payload[32];
        
        // Construct the lightweight payload: "Header,Power,ID,Sequence"
        snprintf(payload, sizeof(payload), "UE07,13,%d,%lu", EMITTER_ID, seq++);
        
        // CSMA (Carrier-Sense Multiple Access) / Listen Before Talk
        // Prevents collisions if Clock Drift causes TDMA windows to overlap
        if (rf95.isChannelActive()) {
            Serial.println("Warning: Channel is busy. Applying CSMA random backoff delay...");
            delay(random(10, 40)); 
        }
        
        // Transmit the packet
        rf95.send((uint8_t*)payload, strlen(payload));
        rf95.waitPacketSent();
        
        Serial.printf("Sent: %s\n", payload);
        
        // Sleep for the remainder of the 500ms cycle to save power and free the channel
        delay(400); 
    } else {
        // Short delay to prevent watchdog timer triggers while waiting for the slot
        delay(5);
    }
}