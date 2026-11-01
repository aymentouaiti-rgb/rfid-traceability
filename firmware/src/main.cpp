/*
 * Lecteur RFID pour le système de traçabilité - Aptiv
 *
 * Lit un tag RFID via le module RC522, puis envoie l'événement
 * au serveur FastAPI (/scan) via WiFi.
 *
 * IMPORTANT - Config à adapter :
 * - LECTEUR_NOM   : nom de CE lecteur, doit correspondre à un lecteur
 *                    déjà créé côté serveur via POST /lecteurs
 *                    (ex: "Depot_1" ou "Ligne_A")
 * - SERVER_URL    : dans Wokwi (simulation locale), utiliser
 *                    "http://host.wokwi.internal:8000/scan"
 *                    Sur une vraie carte ESP32, remplacer par l'IP
 *                    réelle du serveur sur le réseau (ex: "http://192.168.1.50:8000/scan")
 * - WIFI_SSID/PASS : dans Wokwi, le réseau simulé s'appelle "Wokwi-GUEST"
 *                    et n'a pas de mot de passe
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <MFRC522.h>
#include <ArduinoJson.h>

// ---------- Configuration ----------
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
const char* SERVER_URL = "http://host.wokwi.internal:8000/scan";
const char* LECTEUR_NOM = "Ligne_A";  // remis à "Depot_1" pour tester l'autre point de lecture

// ---------- Broches RC522 (SPI) ----------
#define RFID_SS_PIN   5
#define RFID_RST_PIN  22

MFRC522 rfid(RFID_SS_PIN, RFID_RST_PIN);

// Anti-doublon : évite d'envoyer 10x le même scan si le tag reste devant le lecteur
String dernierTagEnvoye = "";
unsigned long dernierEnvoiMillis = 0;
const unsigned long DELAI_ANTI_DOUBLON_MS = 3000;

void connecterWiFi() {
  Serial.print("Connexion au WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nWiFi connecté ! IP: " + WiFi.localIP().toString());
}

String lireUIDTag() {
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

void envoyerScan(const String& tagId) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Erreur : WiFi non connecté, scan ignoré");
    return;
  }

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");

  JsonDocument doc;
  doc["tag_id"] = tagId;
  doc["lecteur_nom"] = LECTEUR_NOM;
  // doc["badge_id"] = "OUV001";  // décommente si tu ajoutes un lecteur de badge ouvrier

  String payload;
  serializeJson(doc, payload);

  Serial.println("Envoi vers le serveur : " + payload);
  int httpCode = http.POST(payload);

  if (httpCode > 0) {
    String reponse = http.getString();
    Serial.println("Réponse serveur (" + String(httpCode) + ") : " + reponse);
  } else {
    Serial.println("Erreur HTTP : " + http.errorToString(httpCode));
  }

  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("=== Système de traçabilité RFID - Lecteur : " + String(LECTEUR_NOM) + " ===");

  SPI.begin();
  rfid.PCD_Init();
  Serial.println("Module RFID initialisé");

  connecterWiFi();

  Serial.println("Prêt à scanner des tags...");
}

void loop() {
  // Vérifie si un nouveau tag est présent
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    delay(100);
    return;
  }

  String tagId = lireUIDTag();
  unsigned long maintenant = millis();

  // Anti-doublon : ignore si c'est le même tag lu il y a moins de 3 secondes
  if (tagId == dernierTagEnvoye && (maintenant - dernierEnvoiMillis) < DELAI_ANTI_DOUBLON_MS) {
    rfid.PICC_HaltA();
    return;
  }

  Serial.println("Tag détecté : " + tagId);
  envoyerScan(tagId);

  dernierTagEnvoye = tagId;
  dernierEnvoiMillis = maintenant;

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}
