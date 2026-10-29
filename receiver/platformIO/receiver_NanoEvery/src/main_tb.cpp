#include <Arduino.h>
#include <SPI.h>
#include "RF24.h"

// Toggle between physical nRF24L01 receiver mode and simulated data mode
#define RUN_TESTBENCH true  // TB

#define CE_PIN 9    // Active-high pin (<1µA)  
#define CSN_PIN 10  // Active-low SPI pin

RF24 radio(CE_PIN, CSN_PIN);
const byte radioAddress[] = "STRN1"; // Must match the transmitter's address

bool isStreamingActive = false;

// Fixed-size data structure to ensure reliable over-the-air communication
struct __attribute__((__packed__)) MeasurementPacket {
    uint16_t sequence;     // Packet counter to detect missed transmissions
    int32_t  raw_value;    // Sensor measurement data
    uint32_t flags;        // Status or error indicators
    uint32_t tx_sample_ms; // Timestamp from the transmitter
};

// TB
// ============================================================================
#if RUN_TESTBENCH == true
    uint16_t testSequence = 0;
    unsigned long testStartTime = 0;
#endif
// ============================================================================

void setup() {
    Serial.begin(115200);
    while (!Serial) { ; } // Wait for USB connection to become live (critical for Nano Every)

    if (!radio.begin()) {   // Initialise Radio-object
        Serial.println("DEBUG: nRF24L01 hardware not found!");
        #if RUN_TESTBENCH == false  // TB
            while (1); // Halt production code if radio fails and testbench is disabled
        #endif
    } else {
        // Configure radio for maximum range and stability
        radio.setPALevel(RF24_PA_MAX);       // Max transmitter/receiver power
        radio.setDataRate(RF24_250KBPS);     // Lower bitrate = longer range and less noise
        radio.setChannel(76);                // Frequency channel outside standard Wi-Fi range
        radio.openReadingPipe(1, radioAddress); // Bind to transmitter address
        radio.startListening();              // Put module into receiver mode
    }

    // TESTBENCH SETUP NOTICE
    // ------------------------------------------------------------------------
    #if RUN_TESTBENCH == true
        Serial.println("DEBUG: Running in UI simulation mode.");
    #endif
    // ------------------------------------------------------------------------

    // Dashboard initialization trigger
    Serial.println("INIT RX - Receiver ready and listening..."); 
}

void loop() {

    // Process incoming serial commands from the dashboard/PC
    // ------------------------------------------------------------------------
    if (Serial.available() > 0) {
        String command = Serial.readStringUntil('\n');
        command.trim(); 

        if (command.equalsIgnoreCase("start")) {
            isStreamingActive = true;
            Serial.println("AWAKE confirmed"); 
            
            #if RUN_TESTBENCH == true
                testStartTime = millis(); // Reset simulation timer
            #endif
        } 
        else if (command.equalsIgnoreCase("stop")) {
            isStreamingActive = false;
            Serial.println("STOP: streaming disabled"); 
        }
    }

    // Only output data to Serial if the dashboard has requested a "start"
    if (isStreamingActive) {
        
        // --- PRODUCTION HARDWARE CODE ---
        // Read actual wireless data packets from the physical transmitter
        if (radio.available()) {
            MeasurementPacket packet;
            radio.read(&packet, sizeof(packet));

            if (packet.tx_sample_ms >= 0) { 
                // Format output string for dashboard ingestion
                Serial.print("LC,2,");
                Serial.print(packet.sequence);
                Serial.print(",");
                Serial.print(packet.raw_value);
                Serial.print(",");
                Serial.print(packet.flags);
                Serial.print(",");
                Serial.println(packet.tx_sample_ms); 
            }
        } 

        // --- TESTBENCH SIMULATOR CODE ---
        // Generate simulated mathematical sensor data if no radio hardware is used
        // ------------------------------------------------------------------------
        #if RUN_TESTBENCH == true
        else {
            unsigned long elapsed = millis() - testStartTime;
            long simulated_raw = 1000000 + (1000000 * sin(elapsed * 0.001)); // Sine wave simulation
            uint32_t simulated_flags = 0;

            // Output simulated data in identical dashboard format
            Serial.print("LC,2,");
            Serial.print(testSequence);
            Serial.print(",");
            Serial.print(simulated_raw);
            Serial.print(",");
            Serial.print(simulated_flags);
            Serial.print(",");
            Serial.println(elapsed); 

            testSequence = (testSequence + 1) & 0xFFFF; // 16-bit rollover protection
            delay(100); // Enforce 10Hz stream rate during simulation
        }
        #endif
        // ------------------------------------------------------------------------
    }
}
