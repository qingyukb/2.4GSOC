#pragma once

#include <windows.h>

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

enum class DeviceType {
    Mouse,
    Keyboard,
    Headset,
    Speaker,
    Gamepad,
    Receiver,
    Other,
};

enum class BatteryStatus {
    Unknown,
    Discharging,
    Charging,
    Full,
    Wired,
    Asleep,
    Unplugged,
    BatteryUnavailable,
};

struct DeviceConfig {
    uint16_t vid = 0;
    uint16_t pid = 0;
    std::wstring name;
    DeviceType type = DeviceType::Other;
};

struct DpiConfig {
    std::array<uint16_t, 6> stages{};
    int currentIndex = 0;
    int usbIndex = 0;
    int profileIndex = 0;
    int stageCount = 0;
    int sensor = 0;
    std::array<uint8_t, 63> rawBody{};

    uint16_t CurrentDpi() const {
        return currentIndex >= 0 && currentIndex < static_cast<int>(stages.size())
            ? stages[static_cast<size_t>(currentIndex)]
            : 0;
    }
};

struct Reading {
    bool ok = false;
    bool present = false;
    bool stale = false;
    int level = -1;
    BatteryStatus status = BatteryStatus::Unknown;
    std::wstring name;
    std::wstring source;
    std::wstring detail;
    uint16_t vid = 0;
    uint16_t pid = 0;
    uint16_t usagePage = 0;
    int xinputStage = -1;
    std::optional<DpiConfig> dpi;
};

struct HidInterface {
    std::wstring path;
    std::wstring instance;
    std::wstring product;
    std::wstring manufacturer;
    uint16_t vid = 0;
    uint16_t pid = 0;
    uint16_t usagePage = 0;
    uint16_t usage = 0;
    uint16_t inputLength = 0;
    uint16_t outputLength = 0;
    uint16_t featureLength = 0;
};

std::vector<HidInterface> EnumerateHid();
Reading ReadDevice(const DeviceConfig& device, const std::vector<HidInterface>& interfaces, bool includeDpi = false);
Reading ReadXInput();
std::optional<DpiConfig> ReadDpi(uint16_t vid, uint16_t pid);
DpiConfig WriteDpi(uint16_t vid, uint16_t pid, int dpi, std::wstring* error);

bool IsKnownBuiltIn(uint16_t vid, uint16_t pid);
std::wstring BuiltInDisplayName(uint16_t vid, uint16_t pid);
DeviceType InferDeviceType(uint16_t vid, uint16_t pid, const std::wstring& name);
std::wstring DeviceTypeKey(DeviceType type);
std::wstring DeviceTypeLabel(DeviceType type);
DeviceType ParseDeviceType(const std::wstring& key);
std::wstring BatteryStatusLabel(const Reading& reading);
std::wstring BatteryStatusKey(const Reading& reading);
std::wstring DeviceKey(uint16_t vid, uint16_t pid);
