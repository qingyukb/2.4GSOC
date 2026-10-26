#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include "devices.hpp"

#include <hidsdi.h>
#include <hidpi.h>
#include <setupapi.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <cwchar>
#include <cwctype>
#include <mutex>
#include <thread>

namespace {

constexpr GUID kHidDeviceInterface = {
    0x4D1E55B2, 0xF16F, 0x11CF, {0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30}};

class UniqueHandle {
public:
    UniqueHandle() = default;
    explicit UniqueHandle(HANDLE handle) : handle_(handle) {}
    ~UniqueHandle() { reset(); }
    UniqueHandle(const UniqueHandle&) = delete;
    UniqueHandle& operator=(const UniqueHandle&) = delete;
    UniqueHandle(UniqueHandle&& other) noexcept : handle_(other.release()) {}
    UniqueHandle& operator=(UniqueHandle&& other) noexcept {
        if (this != &other) reset(other.release());
        return *this;
    }

    HANDLE get() const { return handle_; }
    explicit operator bool() const { return valid(); }
    bool valid() const { return handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE; }
    HANDLE release() {
        HANDLE value = handle_;
        handle_ = nullptr;
        return value;
    }
    void reset(HANDLE handle = nullptr) {
        if (valid()) CloseHandle(handle_);
        handle_ = handle;
    }

private:
    HANDLE handle_ = nullptr;
};

std::wstring WindowsErrorMessage(DWORD code) {
    if (!code) code = GetLastError();
    wchar_t* buffer = nullptr;
    constexpr DWORD flags = FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                            FORMAT_MESSAGE_IGNORE_INSERTS;
    if (FormatMessageW(flags, nullptr, code, 0, reinterpret_cast<wchar_t*>(&buffer), 0, nullptr)) {
        std::wstring text = buffer ? buffer : L"";
        LocalFree(buffer);
        while (!text.empty() &&
               (text.back() == L'\r' || text.back() == L'\n' || text.back() == L' ')) {
            text.pop_back();
        }
        if (!text.empty()) return text;
    }
    return L"Windows 错误 " + std::to_wstring(code);
}

std::wstring HexBytes(const uint8_t* data, size_t count) {
    static constexpr wchar_t digits[] = L"0123456789ABCDEF";
    std::wstring out;
    out.reserve(count * 3);
    for (size_t i = 0; i < count; ++i) {
        if (i) out.push_back(L' ');
        out.push_back(digits[data[i] >> 4]);
        out.push_back(digits[data[i] & 0x0F]);
    }
    return out;
}

std::wstring HexWord(uint16_t value) {
    const uint8_t bytes[2] = {
        static_cast<uint8_t>(value >> 8), static_cast<uint8_t>(value & 0xFF)};
    return HexBytes(bytes, 2);
}

UniqueHandle OpenHid(const std::wstring& path, bool write) {
    const DWORD access = GENERIC_READ | (write ? GENERIC_WRITE : 0);
    HANDLE handle = CreateFileW(path.c_str(), access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr);
    if (handle == INVALID_HANDLE_VALUE) {
        handle = CreateFileW(path.c_str(), access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             nullptr, OPEN_EXISTING, 0, nullptr);
    }
    return UniqueHandle(handle);
}

bool WaitIo(HANDLE handle, OVERLAPPED& overlapped, DWORD timeoutMs, DWORD* bytes, DWORD* error) {
    const DWORD wait = WaitForSingleObject(overlapped.hEvent, timeoutMs);
    if (wait == WAIT_OBJECT_0) {
        if (!GetOverlappedResult(handle, &overlapped, bytes, FALSE)) {
            if (error) *error = GetLastError();
            return false;
        }
        return true;
    }
    CancelIoEx(handle, &overlapped);
    WaitForSingleObject(overlapped.hEvent, 1000);
    DWORD ignored = 0;
    GetOverlappedResult(handle, &overlapped, &ignored, FALSE);
    if (error) *error = wait == WAIT_TIMEOUT ? ERROR_TIMEOUT : GetLastError();
    return false;
}

bool ReadInput(HANDLE handle, std::vector<uint8_t>& report, DWORD timeoutMs,
               DWORD* error = nullptr) {
    if (report.empty()) return false;
    UniqueHandle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!event) {
        if (error) *error = GetLastError();
        return false;
    }
    OVERLAPPED overlapped{};
    overlapped.hEvent = event.get();
    DWORD bytes = 0;
    std::fill(report.begin(), report.end(), uint8_t{0});
    if (ReadFile(handle, report.data(), static_cast<DWORD>(report.size()), &bytes, &overlapped)) {
        report.resize(bytes);
        return true;
    }
    DWORD readError = GetLastError();
    if (readError != ERROR_IO_PENDING) {
        if (error) *error = readError;
        return false;
    }
    if (!WaitIo(handle, overlapped, timeoutMs, &bytes, &readError)) {
        if (error) *error = readError;
        return false;
    }
    report.resize(bytes);
    return true;
}

bool WriteOutput(HANDLE handle, const std::vector<uint8_t>& report, DWORD timeoutMs,
                 DWORD* error = nullptr) {
    if (report.empty()) return false;
    UniqueHandle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!event) {
        if (error) *error = GetLastError();
        return false;
    }
    OVERLAPPED overlapped{};
    overlapped.hEvent = event.get();
    DWORD bytes = 0;
    if (WriteFile(handle, report.data(), static_cast<DWORD>(report.size()), &bytes, &overlapped)) {
        return true;
    }
    DWORD writeError = GetLastError();
    if (writeError == ERROR_IO_PENDING && WaitIo(handle, overlapped, timeoutMs, &bytes, &writeError)) {
        return true;
    }
    if (HidD_SetOutputReport(handle, const_cast<uint8_t*>(report.data()),
                             static_cast<ULONG>(report.size()))) {
        return true;
    }
    if (error) *error = writeError ? writeError : GetLastError();
    return false;
}

void DrainInput(HANDLE handle, size_t reportLength) {
    for (int i = 0; i < 8; ++i) {
        std::vector<uint8_t> discard(std::max<size_t>(reportLength, 64));
        DWORD error = 0;
        if (!ReadInput(handle, discard, 8, &error)) break;
    }
}

std::vector<HidInterface> Candidates(const std::vector<HidInterface>& interfaces,
                                     uint16_t vid, uint16_t pid) {
    std::vector<HidInterface> result;
    for (const auto& item : interfaces) {
        if (item.vid == vid && item.pid == pid) result.push_back(item);
    }
    return result;
}

const HidInterface* FindInterface(const std::vector<HidInterface>& interfaces,
                                  uint16_t vid, uint16_t pid, uint16_t usagePage,
                                  uint16_t usage = 0, uint16_t minInput = 0,
                                  uint16_t minOutput = 0, uint16_t minFeature = 0) {
    for (const auto& item : interfaces) {
        if (item.vid != vid || item.pid != pid) continue;
        if (usagePage && item.usagePage != usagePage) continue;
        if (usage && item.usage != usage) continue;
        if (item.inputLength < minInput || item.outputLength < minOutput ||
            item.featureLength < minFeature) continue;
        return &item;
    }
    return nullptr;
}

// ---------------------------------------------------------------------------
// MCHOSE A7 Pro
// ---------------------------------------------------------------------------

std::mutex g_mchoseMutex;
std::chrono::steady_clock::time_point g_lastMchoseIo = std::chrono::steady_clock::time_point::min();

std::array<uint8_t, 65> MakeMchoseFrame(uint8_t reportId, uint8_t command,
                                        const uint8_t* args = nullptr, size_t argCount = 0) {
    std::array<uint8_t, 65> frame{};
    frame[0] = reportId;
    std::array<uint8_t, 64> payload{};
    payload[0] = command;
    if (args && argCount) {
        std::copy_n(args, std::min(argCount, payload.size() - 1), payload.begin() + 1);
    }
    for (size_t i = 0; i < payload.size(); ++i) frame[i + 1] = payload[i] ^ 0xFF;
    return frame;
}

bool MchoseSend(HANDLE handle, const std::array<uint8_t, 65>& frame,
                const wchar_t* action, std::wstring* error) {
    const auto now = std::chrono::steady_clock::now();
    if (g_lastMchoseIo != std::chrono::steady_clock::time_point::min()) {
        const auto elapsed = now - g_lastMchoseIo;
        constexpr auto minimumGap = std::chrono::milliseconds(25);
        if (elapsed < minimumGap) std::this_thread::sleep_for(minimumGap - elapsed);
    }
    if (!HidD_SetFeature(handle, const_cast<uint8_t*>(frame.data()),
                         static_cast<ULONG>(frame.size()))) {
        if (error) {
            *error = std::wstring(action) + L"失败：" + WindowsErrorMessage(GetLastError()) +
                     L"。请关闭官方驱动后重试。";
        }
        return false;
    }
    g_lastMchoseIo = std::chrono::steady_clock::now();
    std::this_thread::sleep_for(std::chrono::milliseconds(35));
    return true;
}

Reading ReadMchoseBattery(const HidInterface& iface) {
    Reading reading;
    reading.present = true;
    reading.vid = iface.vid;
    reading.pid = iface.pid;
    reading.usagePage = iface.usagePage;
    reading.source = L"MCHOSE A7 Pro 内置协议（Feature 0x11 / 65B）";

    UniqueHandle handle = OpenHid(iface.path, true);
    if (!handle) {
        reading.detail = L"无法打开迈从 HID 接口：" + WindowsErrorMessage(GetLastError());
        return reading;
    }

    std::array<uint8_t, 65> request{};
    request.fill(0xFF);
    request[0] = 0x11;
    request[1] = 0xF9;
    request[2] = 0xFF;

    std::array<uint8_t, 65> last{};
    bool haveLast = false;
    std::wstring lastError;
    std::lock_guard<std::mutex> lock(g_mchoseMutex);

    for (int sendAttempt = 0; sendAttempt < 2; ++sendAttempt) {
        std::wstring sendError;
        if (!MchoseSend(handle.get(), request, L"发送电量查询", &sendError)) {
            lastError = sendError;
            if (sendAttempt == 0) {
                std::this_thread::sleep_for(std::chrono::milliseconds(120));
                continue;
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(120 + sendAttempt * 40));
        for (int receiveIndex = 0; receiveIndex < 6; ++receiveIndex) {
            std::array<uint8_t, 65> report{};
            report[0] = 0x11;
            if (HidD_GetFeature(handle.get(), report.data(), static_cast<ULONG>(report.size()))) {
                last = report;
                haveLast = true;
                if (report[0] == 0x11 && report[1] == 0x06) {
                    std::array<uint8_t, 63> payload{};
                    std::copy(report.begin() + 2, report.end(), payload.begin());
                    for (int variant = 0; variant < 2; ++variant) {
                        std::array<uint8_t, 63> body = payload;
                        if (variant == 1) {
                            for (auto& value : body) value ^= 0xFF;
                        }
                        const uint16_t bodyVid =
                            static_cast<uint16_t>(body[0] | (body[1] << 8));
                        if (bodyVid != iface.vid) continue;
                        int levelIndex = 11;
                        int level = body[levelIndex];
                        if (level > 100) {
                            levelIndex = 9;
                            level = body[levelIndex];
                        }
                        const uint8_t flags = body[10];
                        const uint8_t chargeCode = flags & 0x0F;
                        const uint8_t fullFlag = (flags >> 4) & 0x0F;
                        reading.ok = true;
                        reading.level = level;
                        reading.status = (chargeCode == 2 || fullFlag)
                            ? BatteryStatus::Full
                            : (chargeCode == 1 ? BatteryStatus::Charging
                                               : BatteryStatus::Discharging);
                        reading.detail = (variant == 0 ? L"明文状态体 " : L"XOR 状态体 ") +
                            HexBytes(body.data(), 12) + L"；电量=body[" +
                            std::to_wstring(levelIndex) + L"]；状态=0x" + HexBytes(&flags, 1);
                        return reading;
                    }
                }
            } else {
                lastError = WindowsErrorMessage(GetLastError());
            }
            if (receiveIndex < 5) {
                std::this_thread::sleep_for(std::chrono::milliseconds(35));
            }
        }
        if (sendAttempt == 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(120));
        }
    }

    if (haveLast) {
        reading.detail = L"回复校验失败：" + HexBytes(last.data(), 16);
    } else if (!lastError.empty()) {
        reading.detail = lastError;
    } else {
        reading.detail = L"没有收到电量回复，鼠标可能正在休眠。";
    }
    reading.status = BatteryStatus::Asleep;
    return reading;
}

bool MchoseReadDpiInternal(HANDLE handle, int attempts, DpiConfig* config,
                           std::wstring* error) {
    const auto request = MakeMchoseFrame(0x12, 0x67);
    std::wstring lastError;
    for (int attempt = 0; attempt < std::max(1, attempts); ++attempt) {
        std::wstring sendError;
        if (!MchoseSend(handle, request, L"读取 DPI 配置", &sendError)) {
            lastError = sendError;
        } else {
            std::array<uint8_t, 65> report{};
            report[0] = 0x12;
            if (HidD_GetFeature(handle, report.data(), static_cast<ULONG>(report.size())) &&
                report[0] == 0x12) {
                std::array<uint8_t, 64> decoded{};
                for (size_t i = 0; i < decoded.size(); ++i) {
                    decoded[i] = static_cast<uint8_t>(report[i + 1] ^ 0xFF);
                }
                if (decoded[0] == 0x67) {
                    std::array<uint8_t, 63> body{};
                    std::copy(decoded.begin() + 1, decoded.end(), body.begin());
                    const bool allZero = std::all_of(body.begin(), body.end(),
                        [](uint8_t value) { return value == 0; });
                    if (!allZero) {
                        const int currentIndex = body[2] & 0x0F;
                        if (currentIndex >= 6) {
                            lastError = L"活动 DPI 档位越界：" +
                                        std::to_wstring(currentIndex);
                        } else {
                            DpiConfig result;
                            result.rawBody = body;
                            result.currentIndex = currentIndex;
                            result.usbIndex = body[1] & 0x0F;
                            result.profileIndex = body[0];
                            result.stageCount = body[16];
                            result.sensor = body[17];
                            for (size_t i = 0; i < result.stages.size(); ++i) {
                                result.stages[i] = static_cast<uint16_t>(
                                    body[4 + i * 2] | (body[5 + i * 2] << 8));
                            }
                            if (config) *config = result;
                            return true;
                        }
                    } else {
                        lastError = L"DPI 配置通道返回全零";
                        if (attempt + 1 < attempts) {
                            const uint8_t zero = 0;
                            const auto wake = MakeMchoseFrame(0x11, 0x58, &zero, 1);
                            std::wstring wakeError;
                            MchoseSend(handle, wake, L"唤醒 DPI 配置通道", &wakeError);
                            std::this_thread::sleep_for(
                                std::chrono::milliseconds(150 * (attempt + 1)));
                        }
                    }
                } else {
                    lastError = L"DPI 回复命令不匹配";
                }
            } else {
                lastError = L"未收到 DPI 配置回复";
            }
        }
        if (attempt + 1 < attempts) {
            std::this_thread::sleep_for(std::chrono::milliseconds(60 * (attempt + 1)));
        }
    }
    if (error) {
        *error = L"没有读到 DPI 配置" + (lastError.empty() ? L"。" : L"：" + lastError) +
                 L"。请轻动鼠标或重新插拔接收器后重试。";
    }
    return false;
}

bool DpiConfigMatches(const std::array<uint8_t, 63>& wanted,
                      const std::array<uint8_t, 63>& current) {
    for (size_t i = 0; i < wanted.size(); ++i) {
        if (i == 3) continue;
        if (i == 17) {
            if ((wanted[i] & 0xFE) != (current[i] & 0xFE)) return false;
        } else if (wanted[i] != current[i]) {
            return false;
        }
    }
    return true;
}

bool IsA7Known(uint16_t vid, uint16_t pid) {
    return vid == 0x5253 && pid == 0x1021;
}

bool IsVgnKnown(uint16_t vid, uint16_t pid) {
    return vid == 0x320F && pid == 0x5088;
}

bool IsXboxPid(uint16_t pid) {
    static constexpr uint16_t pids[] = {
        0x02D1, 0x02DD, 0x02E3, 0x02EA, 0x02FD,
        0x0B00, 0x0B05, 0x0B0A, 0x0B12, 0x0B13, 0x0B20,
    };
    return std::find(std::begin(pids), std::end(pids), pid) != std::end(pids);
}

std::optional<Reading> ReadStandardBattery(const HidInterface& iface) {
    UniqueHandle handle = OpenHid(iface.path, false);
    if (!handle) return std::nullopt;

    PHIDP_PREPARSED_DATA preparsed = nullptr;
    if (!HidD_GetPreparsedData(handle.get(), &preparsed) || !preparsed) {
        return std::nullopt;
    }
    struct PreparsedGuard {
        PHIDP_PREPARSED_DATA data;
        ~PreparsedGuard() { if (data) HidD_FreePreparsedData(data); }
    } guard{preparsed};

    USHORT count = 0;
    NTSTATUS status = HidP_GetValueCaps(HidP_Feature, nullptr, &count, preparsed);
    if ((status != HIDP_STATUS_SUCCESS && status != HIDP_STATUS_BUFFER_TOO_SMALL) ||
        count == 0) {
        return std::nullopt;
    }
    std::vector<HIDP_VALUE_CAPS> caps(count);
    status = HidP_GetValueCaps(HidP_Feature, caps.data(), &count, preparsed);
    if (status != HIDP_STATUS_SUCCESS) return std::nullopt;

    for (const auto& cap : caps) {
        const USAGE usage = cap.IsRange ? cap.Range.UsageMin : cap.NotRange.Usage;
        const bool supported =
            (cap.UsagePage == 0x0006 && usage == 0x0020) ||
            (cap.UsagePage == 0x000D && usage == 0x003B) ||
            (cap.UsagePage == 0x0085 && usage == 0x0065);
        if (!supported) continue;

        const size_t length = std::max<size_t>(iface.featureLength, 64);
        std::vector<uint8_t> report(length, 0);
        if (cap.ReportID) report[0] = cap.ReportID;
        if (!HidD_GetFeature(handle.get(), report.data(), static_cast<ULONG>(report.size()))) {
            continue;
        }
        ULONG value = 0;
        if (HidP_GetUsageValue(HidP_Feature, cap.UsagePage, cap.LinkCollection, usage, &value,
                               preparsed, reinterpret_cast<PCHAR>(report.data()),
                               static_cast<ULONG>(report.size())) != HIDP_STATUS_SUCCESS) {
            continue;
        }
        if (value > 100) continue;

        Reading reading;
        reading.ok = true;
        reading.present = true;
        reading.level = static_cast<int>(value);
        reading.status = BatteryStatus::Discharging;
        reading.source = L"标准 HID 电量字段";
        reading.detail = L"UsagePage=0x" + HexWord(cap.UsagePage) +
                         L" Usage=0x" + HexWord(usage);
        reading.vid = iface.vid;
        reading.pid = iface.pid;
        reading.usagePage = iface.usagePage;
        return reading;
    }
    return std::nullopt;
}

// ---------------------------------------------------------------------------
// VGN V98 Pro
// ---------------------------------------------------------------------------

Reading ReadVgnBattery(const HidInterface& iface) {
    Reading reading;
    reading.present = true;
    reading.vid = iface.vid;
    reading.pid = iface.pid;
    reading.usagePage = iface.usagePage;
    reading.source = L"VGN V98 Pro 内置协议（FF1C/64B）";

    if (iface.usagePage != 0xFF1C || iface.usage != 0x0092 ||
        iface.outputLength < 60 || iface.inputLength < 60) {
        reading.detail = L"接口不匹配：UsagePage=0x" + HexWord(iface.usagePage) +
                         L" Usage=0x" + HexWord(iface.usage) + L" IN=" +
                         std::to_wstring(iface.inputLength) + L"B OUT=" +
                         std::to_wstring(iface.outputLength) + L"B";
        return reading;
    }

    UniqueHandle handle = OpenHid(iface.path, true);
    if (!handle) {
        reading.detail = L"无法打开 VGN HID 接口：" + WindowsErrorMessage(GetLastError());
        return reading;
    }

    std::vector<uint8_t> request(std::max<size_t>(iface.outputLength, 64), 0);
    const uint8_t prefix[] = {0x04, 0x00, 0x00, 0x1A, 0x06, 0x00, 0x00, 0x00};
    std::copy(std::begin(prefix), std::end(prefix), request.begin());
    std::vector<uint8_t> last;
    std::wstring lastError;

    for (int attempt = 0; attempt < 3; ++attempt) {
        DrainInput(handle.get(), iface.inputLength);
        DWORD writeError = 0;
        if (!WriteOutput(handle.get(), request, 500, &writeError)) {
            lastError = WindowsErrorMessage(writeError);
            if (attempt == 2) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(120));
            continue;
        }

        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(1200);
        while (std::chrono::steady_clock::now() < deadline) {
            std::vector<uint8_t> report(std::max<size_t>(iface.inputLength, 64), 0);
            DWORD readError = 0;
            if (!ReadInput(handle.get(), report, 250, &readError)) {
                if (readError != ERROR_TIMEOUT && readError != ERROR_OPERATION_ABORTED) {
                    lastError = WindowsErrorMessage(readError);
                }
                continue;
            }
            last = report;
            if (report.size() >= 10 && report[0] == 0x04 && report[1] == 0x00 &&
                report[2] == 0x00 && report[3] == 0x1A && report[4] == 0x06 &&
                report[8] <= 100) {
                reading.ok = true;
                reading.level = report[8];
                reading.status = report[8] >= 100
                    ? BatteryStatus::Full
                    : (report[9] == 1 ? BatteryStatus::Charging
                                      : BatteryStatus::Discharging);
                reading.detail = L"回包 " + HexBytes(report.data(), 12) +
                                 L"；电量=" + std::to_wstring(report[8]) +
                                 L"% 充电=" + std::to_wstring(report[9]);
                return reading;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(80));
    }

    if (!last.empty()) {
        reading.detail = L"回包校验失败：" + HexBytes(last.data(),
            std::min<size_t>(last.size(), 16));
    } else if (!lastError.empty()) {
        reading.detail = lastError;
    } else {
        reading.detail = L"没有收到键盘电量回报，键盘可能正在休眠。";
    }
    reading.status = BatteryStatus::Asleep;
    return reading;
}

// ---------------------------------------------------------------------------
// Xbox / XInput
// ---------------------------------------------------------------------------

struct XInputStateRaw {
    DWORD packetNumber;
    BYTE gamepad[12];
};
struct XInputBatteryInfoRaw {
    BYTE batteryType;
    BYTE batteryLevel;
};
static_assert(sizeof(XInputStateRaw) == 16);
static_assert(sizeof(XInputBatteryInfoRaw) == 2);

constexpr DWORD kXInputDeviceNotConnected = 1167;
constexpr BYTE kXInputBatteryDisconnected = 0;
constexpr BYTE kXInputBatteryWired = 1;
constexpr BYTE kXInputBatteryAlkaline = 2;
constexpr BYTE kXInputBatteryNiMH = 3;
constexpr BYTE kXInputBatteryUnknown = 0xFF;
constexpr BYTE kXInputDevTypeGamepad = 0;

struct XInputApi {
    HMODULE module = nullptr;
    DWORD(WINAPI* getState)(DWORD, XInputStateRaw*) = nullptr;
    DWORD(WINAPI* getBattery)(DWORD, BYTE, XInputBatteryInfoRaw*) = nullptr;
    explicit operator bool() const { return module && getState && getBattery; }
};

XInputApi LoadXInputApi() {
    static XInputApi api;
    static std::once_flag loaded;
    std::call_once(loaded, [] {
        const wchar_t* libraries[] = {L"xinput1_4.dll", L"xinput1_3.dll", L"xinput9_1_0.dll"};
        for (const auto* name : libraries) {
            HMODULE module = LoadLibraryW(name);
            if (!module) continue;
            auto state = reinterpret_cast<decltype(XInputApi::getState)>(
                GetProcAddress(module, "XInputGetState"));
            auto battery = reinterpret_cast<decltype(XInputApi::getBattery)>(
                GetProcAddress(module, "XInputGetBatteryInformation"));
            if (state && battery) {
                api.module = module;
                api.getState = state;
                api.getBattery = battery;
                return;
            }
            FreeLibrary(module);
        }
    });
    return api;
}

// ---------------------------------------------------------------------------
// DPI public API
// ---------------------------------------------------------------------------

std::optional<DpiConfig> ReadMchoseDpi(uint16_t vid, uint16_t pid) {
    if (!IsA7Known(vid, pid)) return std::nullopt;
    const auto interfaces = EnumerateHid();
    for (const auto& iface : interfaces) {
        if (iface.vid != vid || iface.pid != pid || iface.usagePage != 0xFF01 ||
            iface.featureLength < 65) continue;
        UniqueHandle handle = OpenHid(iface.path, true);
        if (!handle) continue;
        DpiConfig config;
        std::lock_guard<std::mutex> lock(g_mchoseMutex);
        if (MchoseReadDpiInternal(handle.get(), 6, &config, nullptr)) {
            return config;
        }
    }
    return std::nullopt;
}

DpiConfig WriteMchoseDpi(uint16_t vid, uint16_t pid, int dpi, std::wstring* error) {
    if (!IsA7Known(vid, pid)) {
        if (error) *error = L"该设备不支持 DPI 修改。";
        return {};
    }
    int target = static_cast<int>(std::lround(static_cast<double>(dpi) / 100.0) * 100.0);
    target = std::clamp(target, 100, 26000);

    const auto interfaces = EnumerateHid();
    const HidInterface* chosen = nullptr;
    for (const auto& iface : interfaces) {
        if (iface.vid == vid && iface.pid == pid && iface.usagePage == 0xFF01 &&
            iface.featureLength >= 65) {
            chosen = &iface;
            break;
        }
    }
    if (!chosen) {
        if (error) *error = L"鼠标未连接，或接收器已拔出。";
        return {};
    }

    UniqueHandle handle = OpenHid(chosen->path, true);
    if (!handle) {
        if (error) {
            *error = L"无法打开 DPI 配置接口：" + WindowsErrorMessage(GetLastError()) +
                     L"。请关闭官方驱动后重试。";
        }
        return {};
    }

    std::lock_guard<std::mutex> lock(g_mchoseMutex);
    DpiConfig original;
    std::wstring readError;
    if (!MchoseReadDpiInternal(handle.get(), 6, &original, &readError)) {
        if (error) *error = readError;
        return {};
    }

    auto body = original.rawBody;
    const int stage = original.currentIndex;
    if (stage < 0 || stage >= 6) {
        if (error) *error = L"当前 DPI 档位无效。";
        return {};
    }
    body[4 + stage * 2] = static_cast<uint8_t>(target & 0xFF);
    body[5 + stage * 2] = static_cast<uint8_t>((target >> 8) & 0xFF);
    body[51 + stage * 2] = static_cast<uint8_t>(target & 0xFF);
    body[52 + stage * 2] = static_cast<uint8_t>((target >> 8) & 0xFF);

    const auto request = MakeMchoseFrame(0x12, 0x57, body.data(), body.size());
    std::wstring sendError;
    if (!MchoseSend(handle.get(), request, L"写入 DPI", &sendError)) {
        if (error) *error = sendError;
        return {};
    }

    DpiConfig last = original;
    for (int attempt = 0; attempt < 5; ++attempt) {
        std::this_thread::sleep_for(std::chrono::milliseconds(80 * (attempt + 1)));
        DpiConfig current;
        if (!MchoseReadDpiInternal(handle.get(), 2, &current, nullptr)) continue;
        last = current;
        if (DpiConfigMatches(body, current.rawBody)) return current;
    }
    if (error) {
        *error = L"配置已发送，但回读未确认（当前 " +
                 std::to_wstring(last.CurrentDpi()) + L" DPI）。请关闭官方驱动后重试。";
    }
    return {};
}

}  // namespace

std::optional<DpiConfig> ReadDpi(uint16_t vid, uint16_t pid) {
    return ReadMchoseDpi(vid, pid);
}

DpiConfig WriteDpi(uint16_t vid, uint16_t pid, int dpi, std::wstring* error) {
    return WriteMchoseDpi(vid, pid, dpi, error);
}

std::vector<HidInterface> EnumerateHid() {
    std::vector<HidInterface> result;
    HDEVINFO devices = SetupDiGetClassDevsW(
        &kHidDeviceInterface, nullptr, nullptr, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    if (devices == INVALID_HANDLE_VALUE) return result;

    struct DeviceInfoGuard {
        HDEVINFO value;
        ~DeviceInfoGuard() { if (value != INVALID_HANDLE_VALUE) SetupDiDestroyDeviceInfoList(value); }
    } guard{devices};

    for (DWORD index = 0;; ++index) {
        SP_DEVICE_INTERFACE_DATA interfaceData{};
        interfaceData.cbSize = sizeof(interfaceData);
        if (!SetupDiEnumDeviceInterfaces(devices, nullptr, &kHidDeviceInterface, index,
                                         &interfaceData)) {
            if (GetLastError() == ERROR_NO_MORE_ITEMS) break;
            continue;
        }

        DWORD required = 0;
        SetupDiGetDeviceInterfaceDetailW(devices, &interfaceData, nullptr, 0, &required, nullptr);
        if (required < sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)) continue;
        std::vector<uint8_t> storage(required);
        auto* detail = reinterpret_cast<SP_DEVICE_INTERFACE_DETAIL_DATA_W*>(storage.data());
        detail->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W);
        if (!SetupDiGetDeviceInterfaceDetailW(devices, &interfaceData, detail, required,
                                              nullptr, nullptr)) continue;

        const std::wstring path = detail->DevicePath;
        UniqueHandle handle = OpenHid(path, false);
        if (!handle) continue;

        HIDD_ATTRIBUTES attributes{};
        attributes.Size = sizeof(attributes);
        if (!HidD_GetAttributes(handle.get(), &attributes)) continue;

        PHIDP_PREPARSED_DATA preparsed = nullptr;
        if (!HidD_GetPreparsedData(handle.get(), &preparsed) || !preparsed) continue;
        HIDP_CAPS caps{};
        const NTSTATUS status = HidP_GetCaps(preparsed, &caps);
        HidD_FreePreparsedData(preparsed);
        if (status != HIDP_STATUS_SUCCESS) continue;

        HidInterface item;
        item.path = path;
        item.instance = path;
        item.vid = attributes.VendorID;
        item.pid = attributes.ProductID;
        item.usagePage = caps.UsagePage;
        item.usage = caps.Usage;
        item.inputLength = caps.InputReportByteLength;
        item.outputLength = caps.OutputReportByteLength;
        item.featureLength = caps.FeatureReportByteLength;

        wchar_t text[256]{};
        if (HidD_GetProductString(handle.get(), text, sizeof(text))) item.product = text;
        std::fill(std::begin(text), std::end(text), L'\0');
        if (HidD_GetManufacturerString(handle.get(), text, sizeof(text))) {
            item.manufacturer = text;
        }
        result.push_back(std::move(item));
    }
    return result;
}

Reading ReadXInput() {
    Reading reading;
    reading.source = L"Windows XInput（四档电量）";
    const XInputApi api = LoadXInputApi();
    if (!api) {
        reading.present = false;
        reading.detail = L"当前系统找不到 XInput 接口。";
        return reading;
    }

    DWORD lastError = 0;
    for (DWORD slot = 0; slot < 4; ++slot) {
        XInputStateRaw state{};
        DWORD stateCode = api.getState(slot, &state);
        XInputBatteryInfoRaw info{};
        DWORD batteryCode = api.getBattery(slot, kXInputDevTypeGamepad, &info);

        if (stateCode == 0 &&
            (batteryCode != 0 || info.batteryType == kXInputBatteryDisconnected ||
             info.batteryType == kXInputBatteryUnknown)) {
            for (int retry = 0; retry < 3; ++retry) {
                std::this_thread::sleep_for(std::chrono::milliseconds(120));
                XInputBatteryInfoRaw retryInfo{};
                const DWORD retryCode =
                    api.getBattery(slot, kXInputDevTypeGamepad, &retryInfo);
                batteryCode = retryCode;
                info = retryInfo;
                if (retryCode == 0 && info.batteryType != kXInputBatteryDisconnected &&
                    info.batteryType != kXInputBatteryUnknown) {
                    break;
                }
            }
        }

        if (stateCode != 0 && batteryCode == kXInputDeviceNotConnected) continue;
        if (stateCode == 0 && batteryCode != 0) {
            reading.present = true;
            reading.status = BatteryStatus::BatteryUnavailable;
            reading.detail = L"XInput 槽位 " + std::to_wstring(slot + 1) +
                             L"；手柄已连接，但电池接口返回错误码 " +
                             std::to_wstring(batteryCode) + L"。";
            reading.xinputStage = -2;
            return reading;
        }
        if (batteryCode != 0) {
            lastError = batteryCode;
            continue;
        }

        if (info.batteryType == kXInputBatteryDisconnected) {
            if (stateCode == 0) {
                reading.present = true;
                reading.status = BatteryStatus::BatteryUnavailable;
                reading.detail = L"XInput 槽位 " + std::to_wstring(slot + 1) +
                                 L"；手柄已连接，本轮未返回电池状态。";
                reading.xinputStage = -2;
                return reading;
            }
            continue;
        }
        if (info.batteryType == kXInputBatteryWired) {
            reading.ok = true;
            reading.present = true;
            reading.level = 100;
            reading.status = BatteryStatus::Wired;
            reading.xinputStage = 4;
            reading.detail = L"XInput 槽位 " + std::to_wstring(slot + 1) +
                             L"；有线连接，不提供电池百分比。";
            return reading;
        }
        if (info.batteryType == kXInputBatteryUnknown) {
            reading.present = true;
            reading.status = BatteryStatus::BatteryUnavailable;
            reading.detail = L"XInput 暂未提供可靠电量；请使用 Xbox 无线适配器。";
            reading.xinputStage = -2;
            return reading;
        }
        if (info.batteryLevel > 3) {
            reading.present = true;
            reading.status = BatteryStatus::BatteryUnavailable;
            reading.detail = L"XInput 返回未知电量档位 " +
                             std::to_wstring(info.batteryLevel) + L"。";
            return reading;
        }

        static constexpr int levels[] = {5, 30, 70, 100};
        static constexpr const wchar_t* levelText[] = {L"空", L"低", L"中", L"满"};
        reading.ok = true;
        reading.present = true;
        reading.level = levels[info.batteryLevel];
        reading.status = BatteryStatus::Discharging;
        reading.xinputStage = info.batteryLevel;
        reading.detail = L"XInput 槽位 " + std::to_wstring(slot + 1) +
                         L"；无线电池；四档电量=" + levelText[info.batteryLevel] +
                         L"（非精确百分比）";
        return reading;
    }

    reading.detail = lastError
        ? L"XInput 返回错误码 " + std::to_wstring(lastError)
        : L"XInput 暂未返回手柄电量，轻按手柄按键后重试。";
    return reading;
}

Reading ReadDevice(const DeviceConfig& device, const std::vector<HidInterface>& interfaces,
                   bool includeDpi) {
    Reading reading;
    reading.vid = device.vid;
    reading.pid = device.pid;
    reading.name = device.name.empty()
        ? BuiltInDisplayName(device.vid, device.pid)
        : device.name;

    if (IsXboxPid(device.pid)) {
        reading = ReadXInput();
        reading.vid = device.vid;
        reading.pid = device.pid;
        if (!reading.name.empty() && !device.name.empty()) reading.name = device.name;
        if (reading.name.empty()) reading.name = BuiltInDisplayName(device.vid, device.pid);
        return reading;
    }

    std::vector<HidInterface> matches = Candidates(interfaces, device.vid, device.pid);
    if (matches.empty()) {
        reading.present = false;
        reading.status = BatteryStatus::Unplugged;
        reading.detail = L"设备未连接，或接收器已拔出。";
        return reading;
    }
    reading.present = true;

    if (IsA7Known(device.vid, device.pid)) {
        const HidInterface* iface = FindInterface(
            matches, device.vid, device.pid, 0xFF01, 0, 0, 0, 65);
        reading = iface ? ReadMchoseBattery(*iface) : reading;
        if (!iface) {
            reading.ok = false;
            reading.status = BatteryStatus::BatteryUnavailable;
            reading.detail = L"没有找到迈从私有 Feature 接口（UsagePage=0xFF01）。";
        }
        reading.name = device.name.empty()
            ? BuiltInDisplayName(device.vid, device.pid)
            : device.name;
        if (includeDpi && reading.ok) reading.dpi = ::ReadDpi(device.vid, device.pid);
        return reading;
    }

    if (IsVgnKnown(device.vid, device.pid)) {
        const HidInterface* iface = FindInterface(
            matches, device.vid, device.pid, 0xFF1C, 0x0092, 60, 60);
        reading = iface ? ReadVgnBattery(*iface) : reading;
        if (!iface) {
            reading.ok = false;
            reading.status = BatteryStatus::BatteryUnavailable;
            reading.detail = L"没有找到 VGN 私有控制接口（FF1C / 0x0092）。";
        }
        reading.name = device.name.empty()
            ? BuiltInDisplayName(device.vid, device.pid)
            : device.name;
        return reading;
    }

    for (const auto& iface : matches) {
        if (auto standard = ReadStandardBattery(iface)) {
            reading = *standard;
            reading.name = device.name.empty() ? iface.product : device.name;
            return reading;
        }
    }
    reading.ok = false;
    reading.status = BatteryStatus::BatteryUnavailable;
    reading.detail = L"设备已连接，但没有可识别的电量字段。";
    reading.name = device.name.empty()
        ? (matches.front().product.empty() ? BuiltInDisplayName(device.vid, device.pid)
                                           : matches.front().product)
        : device.name;
    return reading;
}

bool IsKnownBuiltIn(uint16_t vid, uint16_t pid) {
    return IsA7Known(vid, pid) || IsVgnKnown(vid, pid) ||
           (vid == 0x045E && IsXboxPid(pid));
}

std::wstring BuiltInDisplayName(uint16_t vid, uint16_t pid) {
    if (IsA7Known(vid, pid)) return L"MCHOSE A7 Pro";
    if (IsVgnKnown(vid, pid)) return L"VGN V98 Pro";
    if (vid == 0x045E && IsXboxPid(pid)) return L"Xbox 手柄";
    return L"未知设备";
}

DeviceType InferDeviceType(uint16_t vid, uint16_t pid, const std::wstring& name) {
    if (IsA7Known(vid, pid)) return DeviceType::Mouse;
    if (IsVgnKnown(vid, pid)) return DeviceType::Keyboard;
    if (vid == 0x045E && IsXboxPid(pid)) return DeviceType::Gamepad;

    std::wstring lower = name;
    std::transform(lower.begin(), lower.end(), lower.begin(),
                   [](wchar_t ch) { return static_cast<wchar_t>(std::towlower(ch)); });
    if (lower.find(L"mouse") != std::wstring::npos || lower.find(L"鼠标") != std::wstring::npos)
        return DeviceType::Mouse;
    if (lower.find(L"keyboard") != std::wstring::npos || lower.find(L"键盘") != std::wstring::npos)
        return DeviceType::Keyboard;
    if (lower.find(L"headset") != std::wstring::npos || lower.find(L"耳机") != std::wstring::npos)
        return DeviceType::Headset;
    if (lower.find(L"speaker") != std::wstring::npos || lower.find(L"音箱") != std::wstring::npos)
        return DeviceType::Speaker;
    if (lower.find(L"gamepad") != std::wstring::npos || lower.find(L"controller") != std::wstring::npos ||
        lower.find(L"手柄") != std::wstring::npos) return DeviceType::Gamepad;
    if (lower.find(L"receiver") != std::wstring::npos || lower.find(L"接收器") != std::wstring::npos)
        return DeviceType::Receiver;
    return DeviceType::Other;
}

std::wstring DeviceTypeKey(DeviceType type) {
    switch (type) {
    case DeviceType::Mouse: return L"mouse";
    case DeviceType::Keyboard: return L"keyboard";
    case DeviceType::Headset: return L"headset";
    case DeviceType::Speaker: return L"speaker";
    case DeviceType::Gamepad: return L"gamepad";
    case DeviceType::Receiver: return L"receiver";
    default: return L"other";
    }
}

std::wstring DeviceTypeLabel(DeviceType type) {
    switch (type) {
    case DeviceType::Mouse: return L"鼠标";
    case DeviceType::Keyboard: return L"键盘";
    case DeviceType::Headset: return L"耳机";
    case DeviceType::Speaker: return L"音箱";
    case DeviceType::Gamepad: return L"手柄";
    case DeviceType::Receiver: return L"接收器";
    default: return L"其他";
    }
}

DeviceType ParseDeviceType(const std::wstring& key) {
    if (key == L"mouse") return DeviceType::Mouse;
    if (key == L"keyboard") return DeviceType::Keyboard;
    if (key == L"headset") return DeviceType::Headset;
    if (key == L"speaker") return DeviceType::Speaker;
    if (key == L"gamepad") return DeviceType::Gamepad;
    if (key == L"receiver") return DeviceType::Receiver;
    return DeviceType::Other;
}

std::wstring BatteryStatusKey(const Reading& reading) {
    switch (reading.status) {
    case BatteryStatus::Discharging: return L"discharging";
    case BatteryStatus::Charging: return L"charging";
    case BatteryStatus::Full: return L"full";
    case BatteryStatus::Wired: return L"wired";
    case BatteryStatus::Asleep: return L"asleep";
    case BatteryStatus::Unplugged: return L"unplugged";
    case BatteryStatus::BatteryUnavailable: return L"battery_unavailable";
    default: return L"unknown";
    }
}

std::wstring BatteryStatusLabel(const Reading& reading) {
    switch (reading.status) {
    case BatteryStatus::Discharging: return L"放电中";
    case BatteryStatus::Charging: return L"充电中";
    case BatteryStatus::Full: return L"已充满";
    case BatteryStatus::Wired: return L"有线模式";
    case BatteryStatus::Asleep: return L"休眠 / 无响应";
    case BatteryStatus::Unplugged: return L"未连接";
    case BatteryStatus::BatteryUnavailable: return L"无电量数据";
    default: return L"未知";
    }
}

std::wstring DeviceKey(uint16_t vid, uint16_t pid) {
    wchar_t buffer[16]{};
    swprintf_s(buffer, L"x%04X%04X", vid, pid);
    return buffer;
}
