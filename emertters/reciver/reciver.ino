// Simple LoRa Sniffer - Receiver
// Used to verify Emitters are working correctly
#include <SPI.h>
#include <RH_RF95.h>

// Pin definitions for Adafruit Feather M0 with RFM95W
#define RFM95_CS   8
#define RFM95_INT  3
#define RFM95_RST  4
#define RF95_FREQ  868.1

RH_RF95 rf95(RFM95_CS, RFM95_INT);

void setup() {
  Serial.begin(115200);
  // Wait for Serial Monitor to open before continuing
  while (!Serial); 

  // Hardware reset
  pinMode(RFM95_RST, OUTPUT);
  digitalWrite(RFM95_RST, HIGH);
  digitalWrite(RFM95_RST, LOW);  delay(10);
  digitalWrite(RFM95_RST, HIGH); delay(10);

  if (!rf95.init()) {
    Serial.println("LoRa init failed");
    while(1);
  }

  // Configuration must match the Emitters
  rf95.setFrequency(RF95_FREQ);
  rf95.setModemConfig(RH_RF95::Bw125Cr45Sf128); // SF7, BW 125 kHz
  
  Serial.println("LoRa Sniffer Ready. Listening for beacons...");
}

void loop() {
  // Check if a new packet has arrived in the air
  if (rf95.available()) {
    uint8_t buf[RH_RF95_MAX_MESSAGE_LEN];
    uint8_t len = sizeof(buf);

    // Read the packet
    if (rf95.recv(buf, &len)) {
      int rssi = rf95.lastRssi();
      
      // Print the payload and the signal strength
      Serial.print("Received packet: ");
      Serial.print((char*)buf);
      Serial.print(" | Hardware RSSI: ");
      Serial.println(rssi);
    }
  }
}