#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <windowsx.h>
#include <d2d1.h>
#include <dwrite.h>
#include <dwmapi.h>
#include <shellapi.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <commctrl.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <fstream>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include <wrl/client.h>

#include "devices.hpp"
#include "json.hpp"

#ifdef small
#undef small
#endif
#ifdef DrawText
#undef DrawText
#endif

using Microsoft::WRL::ComPtr;

namespace {

constexpr wchar_t kWindowClass[] = L"EZ24GNativePanel";
constexpr wchar_t kWindowTitle[] = L"外设中心";
constexpr wchar_t kMutexName[] = L"Local\\EZ24GNativePanel";
constexpr UINT kTrayMessage = WM_APP + 1;
constexpr UINT kRefreshDone = WM_APP + 2;
constexpr UINT kDpiDone = WM_APP + 3;
constexpr UINT kShowPanel = WM_APP + 4;
constexpr UINT kExitApp = WM_APP + 5;
constexpr UINT_PTR kAnimationTimer = 1;
constexpr UINT kTrayId = 1;
constexpr UINT kMenuOpen = 1001;
constexpr UINT kMenuExit = 1002;
constexpr UINT kMenuAutostart = 1003;
constexpr UINT kDeviceMenuBase = 2000;
constexpr UINT kMenuRename = 1;
constexpr UINT kMenuDpi = 2;
constexpr UINT kMenuDelete = 3;
constexpr UINT kMenuTypeBase = 10;
constexpr int kLowBatteryThreshold = 20;
constexpr int kLowBatteryRearm = 25;
constexpr int kDpiMin = 100;
constexpr int kDpiMax = 26000;
constexpr int kDpiStep = 100;
constexpr std::array<int, 9> kDpiSnapPresets = {
    400, 800, 1200, 1600, 2400, 3200, 6400, 12800, 26000};
constexpr std::array<int, 7> kDpiVisibleTicks = {
    100, 400, 800, 1600, 3200, 6400, 26000};

#ifndef DWMWA_WINDOW_CORNER_PREFERENCE
#define DWMWA_WINDOW_CORNER_PREFERENCE 33
#endif
#ifndef DWMWCP_ROUND
#define DWMWCP_ROUND 2
#endif
#ifndef DWMWA_NCRENDERING_POLICY
#define DWMWA_NCRENDERING_POLICY 2
#endif
#ifndef DWMNCRP_ENABLED
#define DWMNCRP_ENABLED 2
#endif

struct Palette {
    D2D1_COLOR_F background;
    D2D1_COLOR_F surface;
    D2D1_COLOR_F surfaceAlt;
    D2D1_COLOR_F text;
    D2D1_COLOR_F textMuted;
    D2D1_COLOR_F textFaint;
    D2D1_COLOR_F border;
    D2D1_COLOR_F track;
    D2D1_COLOR_F success;
    D2D1_COLOR_F warning;
    D2D1_COLOR_F danger;
    D2D1_COLOR_F logo;
};

D2D1_COLOR_F Color(uint32_t rgb, float alpha = 1.0f) {
    return D2D1::ColorF(
        static_cast<float>((rgb >> 16) & 0xFF) / 255.0f,
        static_cast<float>((rgb >> 8) & 0xFF) / 255.0f,
        static_cast<float>(rgb & 0xFF) / 255.0f,
        alpha);
}

D2D1_COLOR_F Mix(D2D1_COLOR_F a, D2D1_COLOR_F b, float t) {
    t = std::clamp(t, 0.0f, 1.0f);
    return D2D1::ColorF(
        a.r + (b.r - a.r) * t,
        a.g + (b.g - a.g) * t,
        a.b + (b.b - a.b) * t,
        a.a + (b.a - a.a) * t);
}

D2D1_COLOR_F WithAlpha(D2D1_COLOR_F color, float alpha) {
    color.a = alpha;
    return color;
}

Palette LightPalette() {
    return {
        Color(0xF7F5F0), Color(0xFFFFFF), Color(0xF2F0EB),
        Color(0x24241F), Color(0x66665F), Color(0x9A9A91),
        Color(0xE7E3DA), Color(0xE7E4DC), Color(0x2D8F66),
        Color(0xC77B22), Color(0xD94B4B), Color(0x24241F)};
}

Palette DarkPalette() {
    return {
        Color(0x171816), Color(0x222320), Color(0x2B2C28),
        Color(0xF5F3EC), Color(0xB4B4AC), Color(0x7F8079),
        Color(0x393A35), Color(0x353630), Color(0x5ACB97),
        Color(0xE6A857), Color(0xF07272), Color(0xE9E7E1)};
}

Palette MixPalette(const Palette& a, const Palette& b, float t) {
    return {
        Mix(a.background, b.background, t), Mix(a.surface, b.surface, t),
        Mix(a.surfaceAlt, b.surfaceAlt, t), Mix(a.text, b.text, t),
        Mix(a.textMuted, b.textMuted, t), Mix(a.textFaint, b.textFaint, t),
        Mix(a.border, b.border, t), Mix(a.track, b.track, t),
        Mix(a.success, b.success, t), Mix(a.warning, b.warning, t),
        Mix(a.danger, b.danger, t), Mix(a.logo, b.logo, t)};
}

D2D1_COLOR_F AccentFor(DeviceType type) {
    switch (type) {
    case DeviceType::Mouse: return Color(0x2F80D9);
    case DeviceType::Keyboard: return Color(0x8A5CD6);
    case DeviceType::Headset: return Color(0xE45E93);
    case DeviceType::Speaker: return Color(0x2D9A9A);
    case DeviceType::Gamepad: return Color(0xE47D2E);
    case DeviceType::Receiver: return Color(0x6A7B8C);
    default: return Color(0x5C9A6F);
    }
}

std::wstring Utf8ToWide(const std::string& text) {
    if (text.empty()) return {};
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(),
                                    static_cast<int>(text.size()), nullptr, 0);
    if (count <= 0) count = MultiByteToWideChar(CP_ACP, 0, text.data(),
                                                static_cast<int>(text.size()), nullptr, 0);
    if (count <= 0) return {};
    std::wstring result(static_cast<size_t>(count), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                        result.data(), count);
    return result;
}

std::string WideToUtf8(const std::wstring& text) {
    if (text.empty()) return {};
    int count = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                                    nullptr, 0, nullptr, nullptr);
    if (count <= 0) return {};
    std::string result(static_cast<size_t>(count), '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                        result.data(), count, nullptr, nullptr);
    return result;
}

std::wstring ReadUtf8File(const std::wstring& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) return {};
    std::string data((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (data.size() >= 3 && static_cast<unsigned char>(data[0]) == 0xEF &&
        static_cast<unsigned char>(data[1]) == 0xBB &&
        static_cast<unsigned char>(data[2]) == 0xBF) {
        data.erase(0, 3);
    }
    return Utf8ToWide(data);
}

bool WriteUtf8File(const std::wstring& path, const std::wstring& text) {
    const std::string utf8 = WideToUtf8(text);
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    if (!file) return false;
    file.write(utf8.data(), static_cast<std::streamsize>(utf8.size()));
    return file.good();
}

std::wstring JsonString(const mini_json::Value& object, const wchar_t* key,
                        const std::wstring& fallback = L"") {
    const auto* value = object.Find(key);
    return value && value->IsString() ? value->string : fallback;
}

double JsonNumber(const mini_json::Value& object, const wchar_t* key, double fallback = 0.0) {
    const auto* value = object.Find(key);
    return value && value->IsNumber() ? value->number : fallback;
}

bool PointInRect(float x, float y, const D2D1_RECT_F& rect) {
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

D2D1_RECT_F Inset(const D2D1_RECT_F& rect, float amount) {
    return D2D1::RectF(rect.left + amount, rect.top + amount,
                       rect.right - amount, rect.bottom - amount);
}

float ClampF(float value, float low, float high) {
    return std::max(low, std::min(value, high));
}

std::wstring FormatPercent(const Reading& reading) {
    return reading.level >= 0 ? std::to_wstring(reading.level) + L"%" : L"--";
}

std::wstring TrimForTip(const std::wstring& text, size_t maxChars = 127) {
    if (text.size() <= maxChars) return text;
    if (maxChars <= 3) return text.substr(0, maxChars);
    return text.substr(0, maxChars - 3) + L"...";
}

struct UiDevice {
    DeviceConfig config;
    Reading reading;
    bool lowNotified = false;
    bool dpiExpanded = false;
    bool dpiHover = false;
    bool sliderHover = false;
    bool dpiValueHover = false;
    bool sliderDragging = false;
    float dpiAnimation = 0.0f;
    float hover = 0.0f;
    int draftDpi = 1600;
};

struct TextFormats {
    ComPtr<IDWriteTextFormat> logo;
    ComPtr<IDWriteTextFormat> title;
    ComPtr<IDWriteTextFormat> subtitle;
    ComPtr<IDWriteTextFormat> cardTitle;
    ComPtr<IDWriteTextFormat> body;
    ComPtr<IDWriteTextFormat> small;
    ComPtr<IDWriteTextFormat> tiny;
    ComPtr<IDWriteTextFormat> number;
    ComPtr<IDWriteTextFormat> button;
    ComPtr<IDWriteTextFormat> status;
};

struct CardLayout {
    D2D1_RECT_F card{};
    D2D1_RECT_F icon{};
    D2D1_RECT_F name{};
    D2D1_RECT_F type{};
    D2D1_RECT_F status{};
    D2D1_RECT_F dpiButton{};
    D2D1_RECT_F dpiValueBox{};
    D2D1_RECT_F progress{};
    D2D1_RECT_F dpiPanel{};
    D2D1_RECT_F slider{};
    D2D1_POINT_2F ringCenter{};
    float ringRadius = 34.0f;
    float cardHeight = 0.0f;
};

struct PanelLayout {
    D2D1_RECT_F closeButton{};
    D2D1_RECT_F themeButton{};
    D2D1_RECT_F summary{};
    D2D1_RECT_F viewport{};
    D2D1_RECT_F footer{};
    D2D1_RECT_F minimizeButton{};
    D2D1_RECT_F exitButton{};
    std::vector<CardLayout> cards;
    float contentHeight = 0.0f;
    float maxScroll = 0.0f;
};

struct RefreshResult {
    std::vector<Reading> readings;
    std::chrono::system_clock::time_point finished;
};

struct DpiResult {
    int index = -1;
    bool write = false;
    bool ok = false;
    DpiConfig config{};
    std::wstring error;
};

struct ButtonVisual {
    bool hover = false;
    float hoverAmount = 0.0f;
};

class App;
LRESULT CALLBACK WindowProcedure(HWND, UINT, WPARAM, LPARAM);
}  // namespace

namespace {

class App {
public:
    App(HINSTANCE instance, bool showOnLaunch)
        : instance_(instance), showOnLaunch_(showOnLaunch),
          started_(std::chrono::steady_clock::now()) {}

    ~App() {
        Shutdown();
        ReleaseGdiResources();
    }

    bool Initialize();
    int Run();
    LRESULT HandleMessage(HWND hwnd, UINT message, WPARAM wParam, LPARAM lParam);
    static App* FromWindow(HWND hwnd);

private:
    void CreateWindowAndTray();
    bool InitializeGraphics();
    bool CreateRenderTarget();
    void ReleaseGdiResources();
    void Shutdown();
    void LoadConfig();
    void SeedConnectedDevices();
    void SaveConfig(bool saveWindowSize = false);
    void LoadDevicesFromConfig();
    Reading LoadCachedReading(const DeviceConfig& config) const;
    void LoadTextFormats();
    void CreateBrushes();

    void StartWorkers();
    void StopWorkers();
    void RefreshLoop();
    void RequestRefresh();
    void HandleRefreshResult(RefreshResult* result);
    void BeginDpiRead(int index);
    void BeginDpiWrite(int index, int value);
    void HandleDpiResult(DpiResult* result);

    void ShowPanel();
    void HideToTray();
    void ExitApplication();
    void AddTrayIcon();
    void RemoveTrayIcon();
    void UpdateTrayTooltip();
    void ShowTrayMenu(POINT point);
    void ShowDeviceMenu(int index, POINT point);
    void ShowTypeMenu(int index, POINT point);
    void ShowLowBatteryNotification(const std::vector<std::wstring>& items);
    bool IsAutoStartEnabled() const;
    void ToggleAutoStart();
    void ApplyBorderlessStyle();
    void UpdateEditControl();
    void DestroyEditControl();
    void StartDpiEdit(int index);
    void CommitDpiEdit();
    void CancelDpiEdit();
    void CommitActiveEdit();
    void StartRename(int index);
    void CommitRename();
    void CancelRename();
    void RebuildDeviceConfigsFromUi();

    void BeginAnimation();
    void Animate();
    void Invalidate();

    PanelLayout BuildLayout() const;
    Palette CurrentPalette() const;
    void Render();
    void DrawBackground(const PanelLayout& layout, const Palette& palette);
    void DrawHeader(const PanelLayout& layout, const Palette& palette);
    void DrawSummary(const PanelLayout& layout, const Palette& palette);
    void DrawDeviceCard(int index, const CardLayout& layout, const Palette& palette);
    void DrawDeviceIcon(const D2D1_RECT_F& rect, DeviceType type, D2D1_COLOR_F accent);
    void DrawBatteryRing(const CardLayout& card, const UiDevice& device,
                         const Palette& palette, D2D1_COLOR_F accent);
    void DrawProgressBar(const D2D1_RECT_F& rect, float progress,
                         D2D1_COLOR_F color, const Palette& palette);
    void DrawDpiPanel(int index, const CardLayout& card, const UiDevice& device,
                      const Palette& palette, D2D1_COLOR_F accent);
    void DrawFooter(const PanelLayout& layout, const Palette& palette);
    void DrawThemeIcon(const D2D1_RECT_F& rect, const Palette& palette);
    void DrawCloseIcon(const D2D1_RECT_F& rect, const Palette& palette);
    void DrawMinimizeIcon(const D2D1_RECT_F& rect, const Palette& palette);
    void DrawShadow(const D2D1_RECT_F& rect, float radius, float alpha);

    void DrawText(const std::wstring& text, IDWriteTextFormat* format,
                  const D2D1_RECT_F& rect, D2D1_COLOR_F color,
                  D2D1_DRAW_TEXT_OPTIONS options = D2D1_DRAW_TEXT_OPTIONS_CLIP);
    void FillRounded(const D2D1_RECT_F& rect, float radius, D2D1_COLOR_F color);
    void StrokeRounded(const D2D1_RECT_F& rect, float radius, D2D1_COLOR_F color,
                       float width = 1.0f);

    std::wstring DisplayName(const UiDevice& device) const;
    std::wstring DeviceSubtitle(const UiDevice& device) const;
    D2D1_COLOR_F StatusColor(const UiDevice& device, const Palette& palette,
                             D2D1_COLOR_F accent) const;
    float Scale() const { return static_cast<float>(dpi_) / 96.0f; }
    RECT DipRectToPixels(const D2D1_RECT_F& rect) const;

    void HandleMouseMove(int x, int y);
    void HandleLeftButtonDown(int x, int y);
    void HandleLeftButtonUp(int x, int y);
    void HandleDoubleClick(int x, int y);
    void HandleRightClick(int x, int y);
    LRESULT HandleHitTest(POINT screenPoint);
    void OnDpiChanged(UINT dpi, const RECT* suggested);
    void SaveWindowSize();
    void ShowEditMenuForType(int index, POINT point);

    HINSTANCE instance_ = nullptr;
    HWND hwnd_ = nullptr;
    HANDLE singleInstanceMutex_ = nullptr;
    HICON icon_ = nullptr;
    UINT dpi_ = 96;
    bool showOnLaunch_ = true;
    bool exiting_ = false;
    bool running_ = false;
    bool refreshing_ = false;
    bool refreshRequested_ = false;
    bool dpiWorking_ = false;
    bool trayAdded_ = false;
    bool darkTheme_ = false;
    float themeProgress_ = 0.0f;
    float contentAnimation_ = 0.0f;
    float scrollOffset_ = 0.0f;
    bool scrollbarDragging_ = false;
    float scrollbarDragStartY_ = 0.0f;
    float scrollbarDragStartScroll_ = 0.0f;
    int intervalSeconds_ = 60;
    int editingIndex_ = -1;
    int dpiEditingIndex_ = -1;
    bool cancelRename_ = false;
    std::chrono::steady_clock::time_point started_;
    std::chrono::system_clock::time_point lastUpdated_{};

    ComPtr<ID2D1Factory> d2dFactory_;
    ComPtr<IDWriteFactory> writeFactory_;
    ComPtr<ID2D1HwndRenderTarget> renderTarget_;
    ComPtr<ID2D1SolidColorBrush> brush_;
    ComPtr<ID2D1StrokeStyle> roundStroke_;
    TextFormats text_;

    std::wstring configPath_;
    mini_json::Value configRoot_ = mini_json::Value::MakeObject();
    std::vector<UiDevice> devices_;
    mutable std::mutex stateMutex_;

    std::thread refreshThread_;
    std::thread dpiThread_;
    std::mutex workerMutex_;
    std::condition_variable workerCv_;
    PanelLayout layoutCache_{};
    ButtonVisual closeButton_;
    ButtonVisual themeButton_;
    ButtonVisual minimizeButton_;
    ButtonVisual exitButton_;

    NOTIFYICONDATAW tray_{};
    HFONT editFont_ = nullptr;
    HBRUSH editBrush_ = nullptr;
    HWND editControl_ = nullptr;
};

App* g_app = nullptr;

}  // namespace

namespace {

constexpr UINT kEditCommit = WM_APP + 20;
constexpr UINT kEditCancel = WM_APP + 21;

std::wstring ExecutablePath() {
    std::vector<wchar_t> buffer(32768);
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(),
                                            static_cast<DWORD>(buffer.size()));
    return length ? std::wstring(buffer.data(), length) : std::wstring();
}

std::wstring ConfigDirectory() {
    PWSTR roaming = nullptr;
    std::wstring path;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_RoamingAppData, KF_FLAG_DEFAULT,
                                       nullptr, &roaming))) {
        path = roaming;
        CoTaskMemFree(roaming);
    }
    if (path.empty()) {
        wchar_t fallback[MAX_PATH]{};
        if (GetEnvironmentVariableW(L"APPDATA", fallback, MAX_PATH)) path = fallback;
    }
    if (path.empty()) return L".";
    path += L"\\BatteryTray";
    CreateDirectoryW(path.c_str(), nullptr);
    return path;
}

BatteryStatus ParseBatteryStatus(const std::wstring& key) {
    if (key == L"discharging") return BatteryStatus::Discharging;
    if (key == L"charging") return BatteryStatus::Charging;
    if (key == L"full") return BatteryStatus::Full;
    if (key == L"wired") return BatteryStatus::Wired;
    if (key == L"asleep") return BatteryStatus::Asleep;
    if (key == L"unplugged") return BatteryStatus::Unplugged;
    if (key == L"battery_unavailable") return BatteryStatus::BatteryUnavailable;
    return BatteryStatus::Unknown;
}

float DpiToSlider(int dpi) {
    const double clamped = std::clamp(dpi, kDpiMin, kDpiMax);
    return static_cast<float>(std::log(clamped / static_cast<double>(kDpiMin)) /
                              std::log(static_cast<double>(kDpiMax) /
                                       static_cast<double>(kDpiMin)));
}

int SliderToDpi(float value) {
    const double ratio = std::clamp(static_cast<double>(value), 0.0, 1.0);
    const double raw = static_cast<double>(kDpiMin) *
                       std::pow(static_cast<double>(kDpiMax) /
                                static_cast<double>(kDpiMin), ratio);
    const int dpi = std::clamp(static_cast<int>(std::lround(raw / kDpiStep) * kDpiStep),
                           kDpiMin, kDpiMax);
    for (const int preset : kDpiSnapPresets) {
        if (std::abs(ratio - DpiToSlider(preset)) <= 0.018) return preset;
    }
    return dpi;
}

LRESULT CALLBACK EditSubclassProcedure(HWND hwnd, UINT message, WPARAM wParam,
                                       LPARAM lParam, UINT_PTR, DWORD_PTR) {
    if (message == WM_KEYDOWN) {
        if (wParam == VK_RETURN) {
            PostMessageW(GetParent(hwnd), kEditCommit, 0, 0);
            return 0;
        }
        if (wParam == VK_ESCAPE) {
            PostMessageW(GetParent(hwnd), kEditCancel, 0, 0);
            return 0;
        }
    }
    return DefSubclassProc(hwnd, message, wParam, lParam);
}

bool App::Initialize() {
    singleInstanceMutex_ = CreateMutexW(nullptr, FALSE, kMutexName);
    if (!singleInstanceMutex_) return false;
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        if (HWND existing = FindWindowW(kWindowClass, nullptr)) {
            PostMessageW(existing, kShowPanel, 0, 0);
            SetForegroundWindow(existing);
        }
        CloseHandle(singleInstanceMutex_);
        singleInstanceMutex_ = nullptr;
        return false;
    }

    LoadConfig();
    if (!InitializeGraphics()) return false;
    CreateWindowAndTray();
    if (!hwnd_) return false;
    StartWorkers();
    RequestRefresh();
    if (showOnLaunch_) ShowPanel();
    return true;
}

bool App::InitializeGraphics() {
    if (FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED,
                                 d2dFactory_.ReleaseAndGetAddressOf()))) {
        return false;
    }
    if (FAILED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,
                                   __uuidof(IDWriteFactory),
                                   reinterpret_cast<IUnknown**>(
                                       writeFactory_.ReleaseAndGetAddressOf())))) {
        return false;
    }
    if (FAILED(d2dFactory_->CreateStrokeStyle(
            D2D1::StrokeStyleProperties(D2D1_CAP_STYLE_ROUND, D2D1_CAP_STYLE_ROUND,
                                        D2D1_CAP_STYLE_ROUND, D2D1_LINE_JOIN_ROUND),
            nullptr, 0, roundStroke_.ReleaseAndGetAddressOf()))) {
        return false;
    }
    LoadTextFormats();
    return true;
}

bool App::CreateRenderTarget() {
    if (!hwnd_ || !d2dFactory_) return false;
    RECT client{};
    GetClientRect(hwnd_, &client);
    const D2D1_SIZE_U size = D2D1::SizeU(
        static_cast<UINT32>(std::max(1L, client.right - client.left)),
        static_cast<UINT32>(std::max(1L, client.bottom - client.top)));
    const auto properties = D2D1::RenderTargetProperties(
        D2D1_RENDER_TARGET_TYPE_DEFAULT,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_IGNORE));
    if (FAILED(d2dFactory_->CreateHwndRenderTarget(
            properties, D2D1::HwndRenderTargetProperties(hwnd_, size),
            renderTarget_.ReleaseAndGetAddressOf()))) {
        return false;
    }
    renderTarget_->SetDpi(static_cast<float>(dpi_), static_cast<float>(dpi_));
    renderTarget_->SetAntialiasMode(D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
    renderTarget_->SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_CLEARTYPE);
    CreateBrushes();
    return true;
}

void App::CreateWindowAndTray() {
    WNDCLASSEXW windowClass{};
    windowClass.cbSize = sizeof(windowClass);
    windowClass.style = CS_HREDRAW | CS_VREDRAW | CS_DBLCLKS;
    windowClass.lpfnWndProc = WindowProcedure;
    windowClass.hInstance = instance_;
    windowClass.hIcon = LoadIconW(instance_, MAKEINTRESOURCEW(1));
    if (!windowClass.hIcon) windowClass.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    windowClass.hIconSm = windowClass.hIcon;
    windowClass.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    windowClass.lpszClassName = kWindowClass;
    if (!RegisterClassExW(&windowClass) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        return;
    }

    int width = 720;
    int height = 780;
    if (const auto* sizeValue = configRoot_.Find(L"panel_size");
        sizeValue && sizeValue->IsArray() && sizeValue->array.size() >= 2) {
        width = std::clamp(static_cast<int>(sizeValue->array[0].AsNumber(width)), 520, 1800);
        height = std::clamp(static_cast<int>(sizeValue->array[1].AsNumber(height)), 520, 1400);
    }
    const UINT initialDpi = GetDpiForSystem();
    const int pixelWidth = MulDiv(width, static_cast<int>(initialDpi), 96);
    const int pixelHeight = MulDiv(height, static_cast<int>(initialDpi), 96);
    RECT work{};
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &work, 0);
    const int x = work.left + std::max<int>(0, (work.right - work.left - pixelWidth) / 2);
    const int y = work.top + std::max<int>(0, (work.bottom - work.top - pixelHeight) / 2);

    hwnd_ = CreateWindowExW(
        WS_EX_APPWINDOW, kWindowClass, kWindowTitle,
        WS_POPUP | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX,
        x, y, pixelWidth, pixelHeight, nullptr, nullptr, instance_, this);
    if (!hwnd_) return;

    dpi_ = GetDpiForWindow(hwnd_);
    if (!dpi_) dpi_ = initialDpi;
    DWM_WINDOW_CORNER_PREFERENCE corner = static_cast<DWM_WINDOW_CORNER_PREFERENCE>(DWMWCP_ROUND);
    DwmSetWindowAttribute(hwnd_, DWMWA_WINDOW_CORNER_PREFERENCE, &corner, sizeof(corner));
    const int dark = darkTheme_ ? TRUE : FALSE;
    DwmSetWindowAttribute(hwnd_, 20, &dark, sizeof(dark));
    CreateRenderTarget();
    AddTrayIcon();
    UpdateTrayTooltip();

    ShowWindow(hwnd_, SW_HIDE);
    HICON largeIcon = LoadIconW(instance_, MAKEINTRESOURCEW(1));
    if (largeIcon) {
        SendMessageW(hwnd_, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(largeIcon));
        SendMessageW(hwnd_, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(largeIcon));
    }
}

void App::LoadConfig() {
    configPath_ = ConfigDirectory() + L"\\config.json";
    const std::wstring text = ReadUtf8File(configPath_);
    if (!text.empty()) {
        try {
            configRoot_ = mini_json::Parse(text);
        } catch (...) {
            configRoot_ = mini_json::Value::MakeObject();
        }
    }
    if (!configRoot_.IsObject()) configRoot_ = mini_json::Value::MakeObject();
    darkTheme_ = JsonString(configRoot_, L"panel_theme", L"light") == L"dark";
    themeProgress_ = darkTheme_ ? 1.0f : 0.0f;
    intervalSeconds_ = std::clamp(
        static_cast<int>(JsonNumber(configRoot_, L"interval", 60)), 60, 120);
    LoadDevicesFromConfig();
}

void App::LoadDevicesFromConfig() {
    devices_.clear();
    const auto* names = configRoot_.Find(L"names");
    const auto* rawDevices = configRoot_.Find(L"devices");

    if (rawDevices && rawDevices->IsArray()) {
        for (const auto& item : rawDevices->array) {
            if (!item.IsObject()) continue;
            const int vid = static_cast<int>(JsonNumber(item, L"vid", -1));
            const int pid = static_cast<int>(JsonNumber(item, L"pid", -1));
            if (vid < 0 || vid > 0xFFFF || pid < 0 || pid > 0xFFFF) continue;
            DeviceConfig config;
            config.vid = static_cast<uint16_t>(vid);
            config.pid = static_cast<uint16_t>(pid);
            config.name = JsonString(item, L"name");
            const std::wstring key = DeviceKey(config.vid, config.pid);
            if (names && names->IsObject()) {
                if (const auto* named = names->Find(key); named && named->IsString() &&
                    !named->string.empty()) {
                    config.name = named->string;
                }
            }
            if (config.name.empty()) config.name = BuiltInDisplayName(config.vid, config.pid);
            if (config.name == L"未知设备") {
                config.name = key;
            }
            const std::wstring typeKey = JsonString(item, L"type");
            config.type = typeKey.empty()
                ? InferDeviceType(config.vid, config.pid, config.name)
                : ParseDeviceType(typeKey);
            UiDevice ui;
            ui.config = config;
            ui.reading = LoadCachedReading(config);
            ui.draftDpi = ui.reading.dpi ? ui.reading.dpi->CurrentDpi() : 1600;
            devices_.push_back(std::move(ui));
        }
    }

    if (devices_.empty()) SeedConnectedDevices();
}

void App::SeedConnectedDevices() {
    const auto interfaces = EnumerateHid();
    const auto add = [&](uint16_t vid, uint16_t pid, std::wstring name, DeviceType type) {
        if (std::any_of(devices_.begin(), devices_.end(), [&](const UiDevice& device) {
                return device.config.vid == vid && device.config.pid == pid;
            })) return;
        UiDevice ui;
        ui.config.vid = vid;
        ui.config.pid = pid;
        ui.config.name = std::move(name);
        ui.config.type = type;
        devices_.push_back(std::move(ui));
    };

    for (const auto& iface : interfaces) {
        if (IsKnownBuiltIn(iface.vid, iface.pid)) {
            add(iface.vid, iface.pid, BuiltInDisplayName(iface.vid, iface.pid),
                InferDeviceType(iface.vid, iface.pid, iface.product));
        }
    }
    const Reading xinput = ReadXInput();
    if (xinput.present) add(0x045E, 0x02D1, L"Xbox One", DeviceType::Gamepad);
    if (devices_.empty()) {
        add(0x5253, 0x1021, L"MCHOSE A7 Pro", DeviceType::Mouse);
        add(0x320F, 0x5088, L"VGN V98 Pro", DeviceType::Keyboard);
        add(0x045E, 0x02D1, L"Xbox One", DeviceType::Gamepad);
    }
    for (auto& device : devices_) {
        device.reading.name = device.config.name;
        device.reading.vid = device.config.vid;
        device.reading.pid = device.config.pid;
    }
    SaveConfig();
}

Reading App::LoadCachedReading(const DeviceConfig& config) const {
    Reading reading;
    reading.vid = config.vid;
    reading.pid = config.pid;
    reading.name = config.name;
    const auto* last = configRoot_.Find(L"last");
    if (!last || !last->IsObject()) return reading;
    const auto* cached = last->Find(DeviceKey(config.vid, config.pid));
    if (!cached || !cached->IsObject()) return reading;
    const int level = static_cast<int>(JsonNumber(*cached, L"level", -1));
    reading.level = level >= 0 && level <= 100 ? level : -1;
    reading.status = ParseBatteryStatus(JsonString(*cached, L"status"));
    reading.source = JsonString(*cached, L"source");
    reading.detail = JsonString(*cached, L"detail");
    reading.stale = reading.level >= 0;
    reading.present = reading.status != BatteryStatus::Unplugged &&
                      reading.status != BatteryStatus::Unknown;
    return reading;
}

void App::SaveConfig(bool saveWindowSize) {
    std::lock_guard<std::mutex> lock(stateMutex_);
    mini_json::Value list = mini_json::Value::MakeArray();
    mini_json::Value names = mini_json::Value::MakeObject();
    for (const auto& device : devices_) {
        mini_json::Value entry = mini_json::Value::MakeObject();
        entry.Set(L"vid", mini_json::Value::MakeNumber(device.config.vid));
        entry.Set(L"pid", mini_json::Value::MakeNumber(device.config.pid));
        entry.Set(L"name", mini_json::Value::MakeString(device.config.name));
        entry.Set(L"type", mini_json::Value::MakeString(DeviceTypeKey(device.config.type)));
        list.array.push_back(std::move(entry));
        names.Set(DeviceKey(device.config.vid, device.config.pid),
                  mini_json::Value::MakeString(device.config.name));
    }
    configRoot_.Set(L"devices", std::move(list));
    configRoot_.Set(L"names", std::move(names));
    configRoot_.Set(L"panel_theme",
                    mini_json::Value::MakeString(darkTheme_ ? L"dark" : L"light"));
    configRoot_.Set(L"interval", mini_json::Value::MakeNumber(intervalSeconds_));
    if (saveWindowSize && hwnd_) {
        RECT client{};
        GetClientRect(hwnd_, &client);
        mini_json::Value sizeValue = mini_json::Value::MakeArray();
        sizeValue.array.push_back(mini_json::Value::MakeNumber(
            static_cast<double>(MulDiv(client.right - client.left, 96,
                                       static_cast<int>(dpi_)))));
        sizeValue.array.push_back(mini_json::Value::MakeNumber(
            static_cast<double>(MulDiv(client.bottom - client.top, 96,
                                       static_cast<int>(dpi_)))));
        configRoot_.Set(L"panel_size", std::move(sizeValue));
    }
    const std::wstring tempPath = configPath_ + L".tmp";
    if (WriteUtf8File(tempPath, mini_json::Dump(configRoot_))) {
        MoveFileExW(tempPath.c_str(), configPath_.c_str(),
                    MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
    }
}

void App::SaveWindowSize() {
    SaveConfig(true);
}

void App::LoadTextFormats() {
    if (!writeFactory_) return;
    const auto make = [&](float size, DWRITE_FONT_WEIGHT weight,
                          ComPtr<IDWriteTextFormat>& target) {
        if (FAILED(writeFactory_->CreateTextFormat(
                L"Segoe UI Variable Display", nullptr, weight,
                DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_STRETCH_NORMAL, size,
                L"zh-CN", target.ReleaseAndGetAddressOf()))) {
            writeFactory_->CreateTextFormat(L"Microsoft YaHei UI", nullptr, weight,
                                            DWRITE_FONT_STYLE_NORMAL,
                                            DWRITE_FONT_STRETCH_NORMAL, size,
                                            L"zh-CN",
                                            target.ReleaseAndGetAddressOf());
        }
        if (target) {
            target->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
            target->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_NEAR);
            target->SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP);
        }
    };
    make(13.0f, DWRITE_FONT_WEIGHT_BOLD, text_.logo);
    make(25.0f, DWRITE_FONT_WEIGHT_BOLD, text_.title);
    make(12.0f, DWRITE_FONT_WEIGHT_NORMAL, text_.subtitle);
    make(17.0f, DWRITE_FONT_WEIGHT_SEMI_BOLD, text_.cardTitle);
    make(13.0f, DWRITE_FONT_WEIGHT_NORMAL, text_.body);
    make(11.5f, DWRITE_FONT_WEIGHT_NORMAL, text_.small);
    make(10.0f, DWRITE_FONT_WEIGHT_NORMAL, text_.tiny);
    make(29.0f, DWRITE_FONT_WEIGHT_SEMI_BOLD, text_.number);
    make(12.0f, DWRITE_FONT_WEIGHT_SEMI_BOLD, text_.button);
    make(11.0f, DWRITE_FONT_WEIGHT_SEMI_BOLD, text_.status);
    if (text_.number) text_.number->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
    if (text_.number) text_.number->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
}

void App::CreateBrushes() {
    if (!renderTarget_) return;
    if (!brush_) {
        renderTarget_->CreateSolidColorBrush(Color(0x000000),
                                             brush_.ReleaseAndGetAddressOf());
    }
}

void App::ReleaseGdiResources() {
    DestroyEditControl();
    text_ = {};
    brush_.Reset();
    renderTarget_.Reset();
    roundStroke_.Reset();
    writeFactory_.Reset();
    d2dFactory_.Reset();
}

void App::Shutdown() {
    if (exiting_) return;
    exiting_ = true;
    StopWorkers();
    RemoveTrayIcon();
    if (hwnd_) {
        DestroyWindow(hwnd_);
        hwnd_ = nullptr;
    }
    if (singleInstanceMutex_) {
        CloseHandle(singleInstanceMutex_);
        singleInstanceMutex_ = nullptr;
    }
}

}  // namespace

namespace {

App* App::FromWindow(HWND hwnd) {
    return reinterpret_cast<App*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
}

int App::Run() {
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return static_cast<int>(message.wParam);
}

void App::StartWorkers() {
    running_ = true;
    refreshThread_ = std::thread(&App::RefreshLoop, this);
}

void App::StopWorkers() {
    {
        std::lock_guard<std::mutex> lock(workerMutex_);
        running_ = false;
    }
    workerCv_.notify_all();
    if (refreshThread_.joinable()) refreshThread_.join();
    if (dpiThread_.joinable()) dpiThread_.join();
    dpiWorking_ = false;
}

void App::RequestRefresh() {
    {
        std::lock_guard<std::mutex> lock(workerMutex_);
        refreshRequested_ = true;
    }
    workerCv_.notify_all();
}

void App::RefreshLoop() {
    std::unique_lock<std::mutex> lock(workerMutex_);
    while (running_) {
        workerCv_.wait_for(lock, std::chrono::seconds(intervalSeconds_), [&] {
            return !running_ || refreshRequested_;
        });
        if (!running_) break;
        refreshRequested_ = false;
        refreshing_ = true;
        lock.unlock();

        std::vector<DeviceConfig> configs;
        {
            std::lock_guard<std::mutex> stateLock(stateMutex_);
            configs.reserve(devices_.size());
            for (const auto& device : devices_) configs.push_back(device.config);
        }
        const auto interfaces = EnumerateHid();
        auto* result = new RefreshResult();
        result->finished = std::chrono::system_clock::now();
        result->readings.reserve(configs.size());
        for (const auto& config : configs) {
            result->readings.push_back(ReadDevice(config, interfaces, false));
        }
        if (!PostMessageW(hwnd_, kRefreshDone, 0, reinterpret_cast<LPARAM>(result))) {
            delete result;
        }

        lock.lock();
    }
    refreshing_ = false;
}

void App::HandleRefreshResult(RefreshResult* rawResult) {
    std::unique_ptr<RefreshResult> result(rawResult);
    if (!result) return;
    std::vector<std::wstring> lowBattery;
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        const size_t count = std::min(devices_.size(), result->readings.size());
        for (size_t index = 0; index < count; ++index) {
            UiDevice& device = devices_[index];
            Reading fresh = result->readings[index];
            if (!fresh.ok && fresh.present && device.reading.level >= 0) {
                const int cachedLevel = device.reading.level;
                const BatteryStatus freshStatus = fresh.status;
                const std::wstring freshDetail = fresh.detail;
                const std::wstring oldSource = device.reading.source;
                fresh.level = cachedLevel;
                if (fresh.source.empty()) fresh.source = oldSource;
                fresh.stale = true;
                fresh.status = freshStatus;
                if (fresh.detail.empty()) fresh.detail = freshDetail;
            }
            if (fresh.name.empty() || fresh.name == L"未知设备") {
                fresh.name = device.config.name;
            }
            fresh.vid = device.config.vid;
            fresh.pid = device.config.pid;
            if (!fresh.dpi && device.reading.dpi) fresh.dpi = device.reading.dpi;
            device.reading = std::move(fresh);

            const Reading& current = device.reading;
            if (current.ok && current.present && !current.stale && current.level >= 0) {
                const bool charging = current.status == BatteryStatus::Charging ||
                                      current.status == BatteryStatus::Full ||
                                      current.status == BatteryStatus::Wired;
                if (charging || current.level >= kLowBatteryRearm) {
                    device.lowNotified = false;
                } else if (current.level < kLowBatteryThreshold && !device.lowNotified) {
                    device.lowNotified = true;
                    lowBattery.push_back(
                        DisplayName(device) + L" 电量仅剩 " +
                        std::to_wstring(current.level) + L"%，建议尽快充电");
                }
            }
        }
    }
    lastUpdated_ = result->finished;
    refreshing_ = false;
    UpdateTrayTooltip();
    BeginAnimation();
    Invalidate();
    if (!lowBattery.empty()) ShowLowBatteryNotification(lowBattery);
}

void App::BeginDpiRead(int index) {
    if (dpiWorking_ || index < 0 || index >= static_cast<int>(devices_.size())) return;
    DeviceConfig config;
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        config = devices_[static_cast<size_t>(index)].config;
    }
    if (!(config.vid == 0x5253 && config.pid == 0x1021)) return;
    dpiWorking_ = true;
    if (dpiThread_.joinable()) dpiThread_.join();
    dpiThread_ = std::thread([this, index, config] {
        auto* result = new DpiResult();
        result->index = index;
        result->config = ReadDpi(config.vid, config.pid).value_or(DpiConfig{});
        result->ok = result->config.CurrentDpi() > 0;
        if (!result->ok) result->error = L"暂时无法读取 DPI，请唤醒鼠标后重试。";
        if (!PostMessageW(hwnd_, kDpiDone, 0, reinterpret_cast<LPARAM>(result))) {
            delete result;
        }
    });
}

void App::BeginDpiWrite(int index, int value) {
    if (dpiWorking_ || index < 0 || index >= static_cast<int>(devices_.size())) return;
    DeviceConfig config;
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        config = devices_[static_cast<size_t>(index)].config;
    }
    if (!(config.vid == 0x5253 && config.pid == 0x1021)) return;
    dpiWorking_ = true;
    if (dpiThread_.joinable()) dpiThread_.join();
    dpiThread_ = std::thread([this, index, config, value] {
        auto* result = new DpiResult();
        result->index = index;
        result->write = true;
        std::wstring error;
        result->config = WriteDpi(config.vid, config.pid, value, &error);
        result->ok = result->config.CurrentDpi() > 0;
        result->error = std::move(error);
        if (!PostMessageW(hwnd_, kDpiDone, 0, reinterpret_cast<LPARAM>(result))) {
            delete result;
        }
    });
}

void App::HandleDpiResult(DpiResult* rawResult) {
    std::unique_ptr<DpiResult> result(rawResult);
    if (dpiThread_.joinable()) dpiThread_.join();
    dpiWorking_ = false;
    if (!result || result->index < 0 ||
        result->index >= static_cast<int>(devices_.size())) return;
    if (result->ok) {
        std::lock_guard<std::mutex> lock(stateMutex_);
        UiDevice& device = devices_[static_cast<size_t>(result->index)];
        device.reading.dpi = result->config;
        device.draftDpi = result->config.CurrentDpi();
    } else if (result->write && !result->error.empty()) {
        MessageBoxW(hwnd_, result->error.c_str(), L"DPI 设置失败",
                    MB_OK | MB_ICONWARNING | MB_TOPMOST);
        std::lock_guard<std::mutex> lock(stateMutex_);
        UiDevice& device = devices_[static_cast<size_t>(result->index)];
        if (device.reading.dpi) device.draftDpi = device.reading.dpi->CurrentDpi();
    }
    Invalidate();
}

void App::ShowPanel() {
    if (!hwnd_) return;
    ShowWindow(hwnd_, SW_SHOWNORMAL);
    SetForegroundWindow(hwnd_);
    contentAnimation_ = 0.0f;
    BeginAnimation();
    Invalidate();
}

void App::HideToTray() {
    CommitActiveEdit();
    if (hwnd_) ShowWindow(hwnd_, SW_HIDE);
}

void App::ExitApplication() {
    if (exiting_) return;
    exiting_ = true;
    StopWorkers();
    RemoveTrayIcon();
    if (hwnd_) DestroyWindow(hwnd_);
    PostQuitMessage(0);
}

void App::AddTrayIcon() {
    if (!hwnd_ || trayAdded_) return;
    tray_ = {};
    tray_.cbSize = sizeof(tray_);
    tray_.hWnd = hwnd_;
    tray_.uID = kTrayId;
    tray_.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    tray_.uCallbackMessage = kTrayMessage;
    tray_.hIcon = icon_ ? icon_ : LoadIconW(instance_, MAKEINTRESOURCEW(1));
    if (!tray_.hIcon) tray_.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    wcscpy_s(tray_.szTip, L"外设中心");
    if (Shell_NotifyIconW(NIM_ADD, &tray_)) {
        trayAdded_ = true;
        tray_.uVersion = NOTIFYICON_VERSION_4;
        Shell_NotifyIconW(NIM_SETVERSION, &tray_);
        UpdateTrayTooltip();
    }
}

void App::RemoveTrayIcon() {
    if (!trayAdded_) return;
    Shell_NotifyIconW(NIM_DELETE, &tray_);
    trayAdded_ = false;
}

void App::UpdateTrayTooltip() {
    if (!trayAdded_) return;
    std::wstring tip;
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        for (const auto& device : devices_) {
            if (!tip.empty()) tip += L"\n";
            std::wstring line = DisplayName(device) + L"  ";
            line += device.reading.level >= 0
                ? std::to_wstring(device.reading.level) + L"%"
                : L"--";
            const std::wstring status = BatteryStatusLabel(device.reading);
            if (device.reading.status != BatteryStatus::Discharging &&
                !status.empty() && status != L"未知") {
                line += L" · " + status;
            }
            tip += line;
        }
    }
    if (tip.empty()) tip = L"暂无外设";
    tip = TrimForTip(tip);
    wcscpy_s(tray_.szTip, tip.c_str());
    tray_.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP | NIF_SHOWTIP;
    Shell_NotifyIconW(NIM_MODIFY, &tray_);
}

void App::ShowTrayMenu(POINT point) {
    HMENU menu = CreatePopupMenu();
    if (!menu) return;
    AppendMenuW(menu, MF_STRING, kMenuOpen, L"打开外设中心");
    SetMenuDefaultItem(menu, kMenuOpen, FALSE);
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING | (IsAutoStartEnabled() ? MF_CHECKED : MF_UNCHECKED),
                kMenuAutostart, L"开机自动启动");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, kMenuExit, L"退出");
    SetForegroundWindow(hwnd_);
    const UINT command = TrackPopupMenuEx(
        menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
        point.x, point.y, hwnd_, nullptr);
    DestroyMenu(menu);
    PostMessageW(hwnd_, WM_NULL, 0, 0);
    if (command == kMenuOpen) ShowPanel();
    else if (command == kMenuAutostart) ToggleAutoStart();
    else if (command == kMenuExit) ExitApplication();
}

void App::ShowDeviceMenu(int index, POINT point) {
    if (index < 0 || index >= static_cast<int>(devices_.size())) return;
    HMENU menu = CreatePopupMenu();
    HMENU typeMenu = CreatePopupMenu();
    if (!menu || !typeMenu) {
        if (menu) DestroyMenu(menu);
        if (typeMenu) DestroyMenu(typeMenu);
        return;
    }
    AppendMenuW(menu, MF_STRING, kMenuRename, L"重命名");
    const DeviceType currentType = devices_[static_cast<size_t>(index)].config.type;
    const DeviceType types[] = {
        DeviceType::Mouse, DeviceType::Keyboard, DeviceType::Headset,
        DeviceType::Speaker, DeviceType::Gamepad, DeviceType::Receiver,
        DeviceType::Other};
    for (size_t i = 0; i < std::size(types); ++i) {
        AppendMenuW(typeMenu, MF_STRING |
                    (types[i] == currentType ? MF_CHECKED : MF_UNCHECKED),
                    kMenuTypeBase + static_cast<UINT>(i), DeviceTypeLabel(types[i]).c_str());
    }
    AppendMenuW(menu, MF_POPUP, reinterpret_cast<UINT_PTR>(typeMenu), L"设备类型");
    if (devices_[static_cast<size_t>(index)].config.vid == 0x5253 &&
        devices_[static_cast<size_t>(index)].config.pid == 0x1021) {
        AppendMenuW(menu, MF_STRING, kMenuDpi, L"DPI 设置");
    }
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, kMenuDelete, L"删除设备");
    SetForegroundWindow(hwnd_);
    const UINT command = TrackPopupMenuEx(
        menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
        point.x, point.y, hwnd_, nullptr);
    DestroyMenu(menu);
    if (command == kMenuRename) StartRename(index);
    else if (command == kMenuDpi) {
        devices_[static_cast<size_t>(index)].dpiExpanded = true;
        BeginDpiRead(index);
        BeginAnimation();
        Invalidate();
    } else if (command == kMenuDelete) {
        {
            std::lock_guard<std::mutex> lock(stateMutex_);
            if (index >= 0 && index < static_cast<int>(devices_.size())) {
                devices_.erase(devices_.begin() + index);
            }
        }
        SaveConfig();
        UpdateTrayTooltip();
        Invalidate();
    } else if (command >= kMenuTypeBase && command < kMenuTypeBase + 7) {
        devices_[static_cast<size_t>(index)].config.type =
            types[static_cast<size_t>(command - kMenuTypeBase)];
        SaveConfig();
        Invalidate();
    }
}

void App::ShowTypeMenu(int index, POINT point) {
    ShowDeviceMenu(index, point);
}

void App::ShowLowBatteryNotification(const std::vector<std::wstring>& items) {
    if (!trayAdded_ || items.empty()) return;
    std::wstring text;
    for (const auto& item : items) {
        if (!text.empty()) text += L"\n";
        text += item;
    }
    if (text.size() > 255) text.resize(252), text += L"...";
    tray_.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP | NIF_INFO;
    wcscpy_s(tray_.szInfoTitle, L"低电量提醒");
    wcscpy_s(tray_.szInfo, text.c_str());
    tray_.dwInfoFlags = NIIF_WARNING;
    Shell_NotifyIconW(NIM_MODIFY, &tray_);
    tray_.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
}

bool App::IsAutoStartEnabled() const {
    HKEY key = nullptr;
    if (RegOpenKeyExW(HKEY_CURRENT_USER,
                      L"Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                      0, KEY_READ, &key) != ERROR_SUCCESS) return false;
    wchar_t value[32768]{};
    DWORD bytes = sizeof(value);
    DWORD type = 0;
    const LSTATUS status = RegQueryValueExW(key, L"EZ24GBattery", nullptr, &type,
                                            reinterpret_cast<BYTE*>(value), &bytes);
    RegCloseKey(key);
    return status == ERROR_SUCCESS && (type == REG_SZ || type == REG_EXPAND_SZ);
}

void App::ToggleAutoStart() {
    HKEY key = nullptr;
    if (RegCreateKeyExW(HKEY_CURRENT_USER,
                        L"Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                        0, nullptr, 0, KEY_SET_VALUE, nullptr, &key, nullptr) !=
        ERROR_SUCCESS) return;
    if (IsAutoStartEnabled()) {
        RegDeleteValueW(key, L"EZ24GBattery");
    } else {
        const std::wstring command = L"\"" + ExecutablePath() + L"\" --hidden";
        RegSetValueExW(key, L"EZ24GBattery", 0, REG_SZ,
                       reinterpret_cast<const BYTE*>(command.c_str()),
                       static_cast<DWORD>((command.size() + 1) * sizeof(wchar_t)));
    }
    RegCloseKey(key);
}

void App::ApplyBorderlessStyle() {
    if (!hwnd_) return;
    DWM_WINDOW_CORNER_PREFERENCE corner = static_cast<DWM_WINDOW_CORNER_PREFERENCE>(DWMWCP_ROUND);
    DwmSetWindowAttribute(hwnd_, DWMWA_WINDOW_CORNER_PREFERENCE, &corner, sizeof(corner));
    SetWindowPos(hwnd_, nullptr, 0, 0, 0, 0,
                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED);
}

void App::RebuildDeviceConfigsFromUi() {
    std::lock_guard<std::mutex> lock(stateMutex_);
    for (auto& device : devices_) {
        device.reading.name = device.config.name;
        device.reading.vid = device.config.vid;
        device.reading.pid = device.config.pid;
    }
}

}  // namespace

namespace {

void App::DestroyEditControl() {
    if (editControl_) {
        HWND control = editControl_;
        editControl_ = nullptr;
        RemoveWindowSubclass(control, EditSubclassProcedure, 1);
        DestroyWindow(control);
    }
    if (editFont_) DeleteObject(editFont_);
    if (editBrush_) DeleteBrush(editBrush_);
    editFont_ = nullptr;
    editBrush_ = nullptr;
    editingIndex_ = -1;
    dpiEditingIndex_ = -1;
    cancelRename_ = false;
}

void App::StartRename(int index) {
    if (index < 0 || index >= static_cast<int>(devices_.size())) return;
    CommitActiveEdit();
    editingIndex_ = index;
    cancelRename_ = false;
    layoutCache_ = BuildLayout();
    if (index >= static_cast<int>(layoutCache_.cards.size())) {
        DestroyEditControl();
        return;
    }
    RECT rect = DipRectToPixels(layoutCache_.cards[static_cast<size_t>(index)].name);
    InflateRect(&rect, -2, 0);

    editFont_ = CreateFontW(
        -MulDiv(17, static_cast<int>(dpi_), 96), 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE,
        FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI Variable Display");
    if (!editFont_) {
        editFont_ = CreateFontW(-MulDiv(17, static_cast<int>(dpi_), 96), 0, 0, 0,
                                FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE,
                                L"Microsoft YaHei UI");
    }
    const D2D1_COLOR_F surface = CurrentPalette().surface;
    editBrush_ = CreateSolidBrush(RGB(
        static_cast<BYTE>(std::lround(surface.r * 255.0f)),
        static_cast<BYTE>(std::lround(surface.g * 255.0f)),
        static_cast<BYTE>(std::lround(surface.b * 255.0f))));
    editControl_ = CreateWindowExW(
        0, L"EDIT", L"",
        WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | ES_AUTOHSCROLL | ES_LEFT,
        rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top,
        hwnd_, nullptr, instance_, nullptr);
    if (!editControl_) {
        DestroyEditControl();
        return;
    }
    SendMessageW(editControl_, WM_SETFONT, reinterpret_cast<WPARAM>(editFont_), TRUE);
    SendMessageW(editControl_, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN,
                 MAKELPARAM(0, 0));
    SendMessageW(editControl_, EM_SETLIMITTEXT, 64, 0);
    SetWindowTextW(editControl_, devices_[static_cast<size_t>(index)].config.name.c_str());
    SetWindowSubclass(editControl_, EditSubclassProcedure, 1, 0);
    SetFocus(editControl_);
    SendMessageW(editControl_, EM_SETSEL, 0, -1);
}

void App::CommitRename() {
    if (!editControl_ || editingIndex_ < 0) return;
    wchar_t value[256]{};
    GetWindowTextW(editControl_, value, static_cast<int>(std::size(value)));
    std::wstring name = value;
    const auto first = name.find_first_not_of(L" \t\r\n");
    const auto last = name.find_last_not_of(L" \t\r\n");
    name = first == std::wstring::npos ? L"" : name.substr(first, last - first + 1);
    if (!name.empty() && editingIndex_ < static_cast<int>(devices_.size())) {
        devices_[static_cast<size_t>(editingIndex_)].config.name = name;
        devices_[static_cast<size_t>(editingIndex_)].reading.name = name;
        SaveConfig();
        UpdateTrayTooltip();
    }
    DestroyEditControl();
    Invalidate();
}

void App::CancelRename() {
    DestroyEditControl();
    SetFocus(hwnd_);
    Invalidate();
}

void App::StartDpiEdit(int index) {
    if (dpiWorking_ || index < 0 || index >= static_cast<int>(devices_.size())) return;
    CommitActiveEdit();
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        UiDevice& device = devices_[static_cast<size_t>(index)];
        if (!(device.config.vid == 0x5253 && device.config.pid == 0x1021) ||
            !device.dpiExpanded) {
            return;
        }
        if (device.reading.dpi) {
            device.draftDpi = device.reading.dpi->CurrentDpi();
        }
    }

    layoutCache_ = BuildLayout();
    if (index >= static_cast<int>(layoutCache_.cards.size())) return;
    dpiEditingIndex_ = index;
    cancelRename_ = false;
    RECT rect = DipRectToPixels(layoutCache_.cards[static_cast<size_t>(index)].dpiValueBox);
    InflateRect(&rect, -1, -1);

    editFont_ = CreateFontW(
        -MulDiv(14, static_cast<int>(dpi_), 96), 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE,
        FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI Variable Display");
    if (!editFont_) {
        editFont_ = CreateFontW(-MulDiv(14, static_cast<int>(dpi_), 96), 0, 0, 0,
                                FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE,
                                L"Microsoft YaHei UI");
    }
    const D2D1_COLOR_F surface = CurrentPalette().surface;
    editBrush_ = CreateSolidBrush(RGB(
        static_cast<BYTE>(std::lround(surface.r * 255.0f)),
        static_cast<BYTE>(std::lround(surface.g * 255.0f)),
        static_cast<BYTE>(std::lround(surface.b * 255.0f))));
    editControl_ = CreateWindowExW(
        0, L"EDIT", L"",
        WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | ES_AUTOHSCROLL |
            ES_CENTER | ES_NUMBER,
        rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top,
        hwnd_, nullptr, instance_, nullptr);
    if (!editControl_) {
        DestroyEditControl();
        return;
    }
    SendMessageW(editControl_, WM_SETFONT, reinterpret_cast<WPARAM>(editFont_), TRUE);
    SendMessageW(editControl_, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN,
                 MAKELPARAM(4, 4));
    SendMessageW(editControl_, EM_SETLIMITTEXT, 5, 0);
    SetWindowTextW(editControl_,
                   std::to_wstring(devices_[static_cast<size_t>(index)].draftDpi).c_str());
    SetWindowSubclass(editControl_, EditSubclassProcedure, 1, 0);
    SetFocus(editControl_);
    SendMessageW(editControl_, EM_SETSEL, 0, -1);
}

void App::CommitDpiEdit() {
    if (!editControl_ || dpiEditingIndex_ < 0) return;
    const int index = dpiEditingIndex_;
    wchar_t value[16]{};
    GetWindowTextW(editControl_, value, static_cast<int>(std::size(value)));

    const std::wstring input = value;
    int requested = 0;
    try {
        size_t consumed = 0;
        requested = std::stoi(input, &consumed);
        if (consumed == 0 || consumed != input.size()) requested = 0;
    } catch (...) {
        requested = 0;
    }

    bool writeNeeded = false;
    if (index >= 0 && index < static_cast<int>(devices_.size())) {
        std::lock_guard<std::mutex> lock(stateMutex_);
        UiDevice& device = devices_[static_cast<size_t>(index)];
        if (requested <= 0) requested = device.draftDpi;
        requested = std::clamp(
            static_cast<int>(std::lround(requested / static_cast<double>(kDpiStep))) *
                kDpiStep,
            kDpiMin, kDpiMax);
        const int previous = device.reading.dpi
            ? device.reading.dpi->CurrentDpi() : 0;
        device.draftDpi = requested;
        writeNeeded = requested != previous;
    }

    DestroyEditControl();
    if (writeNeeded) BeginDpiWrite(index, requested);
    Invalidate();
}

void App::CancelDpiEdit() {
    const int index = dpiEditingIndex_;
    if (index >= 0 && index < static_cast<int>(devices_.size())) {
        std::lock_guard<std::mutex> lock(stateMutex_);
        UiDevice& device = devices_[static_cast<size_t>(index)];
        if (device.reading.dpi) {
            device.draftDpi = device.reading.dpi->CurrentDpi();
        }
    }
    DestroyEditControl();
    SetFocus(hwnd_);
    Invalidate();
}

void App::CommitActiveEdit() {
    if (dpiEditingIndex_ >= 0) {
        CommitDpiEdit();
    } else if (editingIndex_ >= 0) {
        CommitRename();
    } else if (editControl_) {
        DestroyEditControl();
        Invalidate();
    }
}

void App::UpdateEditControl() {
    if (!editControl_) return;
    D2D1_RECT_F target{};
    if (dpiEditingIndex_ >= 0 &&
        dpiEditingIndex_ < static_cast<int>(layoutCache_.cards.size())) {
        target = layoutCache_.cards[static_cast<size_t>(dpiEditingIndex_)].dpiValueBox;
    } else if (editingIndex_ >= 0 &&
               editingIndex_ < static_cast<int>(layoutCache_.cards.size())) {
        target = layoutCache_.cards[static_cast<size_t>(editingIndex_)].name;
    } else {
        return;
    }
    RECT rect = DipRectToPixels(target);
    InflateRect(&rect, -2, 0);
    SetWindowPos(editControl_, HWND_TOP, rect.left, rect.top,
                 rect.right - rect.left, rect.bottom - rect.top,
                 SWP_NOACTIVATE);
}

void App::HandleDoubleClick(int x, int y) {
    layoutCache_ = BuildLayout();
    const float px = static_cast<float>(x) / Scale();
    const float py = static_cast<float>(y) / Scale();
    for (size_t i = 0; i < layoutCache_.cards.size(); ++i) {
        if (PointInRect(px, py, layoutCache_.cards[i].name)) {
            StartRename(static_cast<int>(i));
            return;
        }
    }
}

}  // namespace

namespace {

PanelLayout App::BuildLayout() const {
    PanelLayout layout;
    RECT client{};
    GetClientRect(hwnd_, &client);
    const float width = static_cast<float>(client.right - client.left) / Scale();
    const float height = static_cast<float>(client.bottom - client.top) / Scale();
    const float margin = 20.0f;

    layout.themeButton = D2D1::RectF(width - 116.0f, 22.0f,
                                     width - 72.0f, 66.0f);
    layout.closeButton = D2D1::RectF(width - 64.0f, 22.0f,
                                     width - 20.0f, 66.0f);
    layout.summary = D2D1::RectF(margin, 92.0f, width - margin, 150.0f);
    layout.viewport = D2D1::RectF(margin, 164.0f, width - margin, height - 80.0f);
    layout.footer = D2D1::RectF(margin, height - 62.0f,
                                width - margin, height - 18.0f);
    layout.exitButton = D2D1::RectF(width - 100.0f, height - 58.0f,
                                    width - 24.0f, height - 20.0f);
    layout.minimizeButton = D2D1::RectF(width - 188.0f, height - 58.0f,
                                        width - 112.0f, height - 20.0f);

    const float viewportHeight = std::max(1.0f, layout.viewport.bottom -
                                                    layout.viewport.top);
    float top = layout.viewport.top - scrollOffset_;
    for (const auto& device : devices_) {
        CardLayout card;
        const bool expanded = device.dpiExpanded;
        card.cardHeight = expanded ? 272.0f : 150.0f;
        card.card = D2D1::RectF(margin, top, width - margin, top + card.cardHeight);
        card.icon = D2D1::RectF(card.card.left + 16.0f, card.card.top + 18.0f,
                                card.card.left + 68.0f, card.card.top + 70.0f);
        card.name = D2D1::RectF(card.card.left + 84.0f, card.card.top + 15.0f,
                                card.card.right - 122.0f, card.card.top + 48.0f);
        card.type = D2D1::RectF(card.card.left + 84.0f, card.card.top + 49.0f,
                                card.card.left + 240.0f, card.card.top + 71.0f);
        card.status = D2D1::RectF(card.card.left + 84.0f, card.card.top + 77.0f,
                                  card.card.left + 152.0f, card.card.top + 103.0f);
        card.dpiButton = D2D1::RectF(card.card.left + 160.0f, card.card.top + 76.0f,
                                     card.card.left + 260.0f, card.card.top + 104.0f);
        card.progress = D2D1::RectF(card.card.left + 84.0f, card.card.top + 120.0f,
                                    card.card.right - 24.0f, card.card.top + 128.0f);
        card.dpiPanel = D2D1::RectF(card.card.left + 16.0f, card.card.top + 160.0f,
                                    card.card.right - 16.0f, card.card.top + 238.0f);
        card.dpiValueBox = D2D1::RectF(card.card.right - 112.0f,
                                      card.dpiPanel.top + 9.0f,
                                      card.card.right - 20.0f,
                                      card.dpiPanel.top + 39.0f);
        card.slider = D2D1::RectF(card.dpiPanel.left + 20.0f,
                                  card.dpiPanel.top + 58.0f,
                                  card.dpiPanel.right - 20.0f,
                                  card.dpiPanel.top + 62.0f);
        card.ringCenter = D2D1::Point2F(card.card.right - 62.0f,
                                        card.card.top + 61.0f);
        card.ringRadius = 34.0f;
        layout.cards.push_back(card);
        top += card.cardHeight + 12.0f;
    }
    layout.contentHeight = top + scrollOffset_ - layout.viewport.top;
    layout.maxScroll = std::max(0.0f, layout.contentHeight - viewportHeight);
    return layout;
}

Palette App::CurrentPalette() const {
    return MixPalette(LightPalette(), DarkPalette(), themeProgress_);
}

std::wstring App::DisplayName(const UiDevice& device) const {
    if (!device.config.name.empty()) return device.config.name;
    if (!device.reading.name.empty() && device.reading.name != L"未知设备") {
        return device.reading.name;
    }
    return DeviceKey(device.config.vid, device.config.pid);
}

std::wstring App::DeviceSubtitle(const UiDevice& device) const {
    std::wstring subtitle = DeviceTypeLabel(device.config.type) + L"  ·  ";
    wchar_t ids[24]{};
    swprintf_s(ids, L"%04X:%04X", device.config.vid, device.config.pid);
    subtitle += ids;
    return subtitle;
}

D2D1_COLOR_F App::StatusColor(const UiDevice& device, const Palette& palette,
                              D2D1_COLOR_F accent) const {
    if (device.reading.status == BatteryStatus::Charging ||
        device.reading.status == BatteryStatus::Full ||
        device.reading.status == BatteryStatus::Wired) {
        return palette.success;
    }
    if (device.reading.level >= 0 && device.reading.level < kLowBatteryThreshold) {
        return palette.danger;
    }
    if (device.reading.stale || !device.reading.present) return palette.textFaint;
    return accent;
}

RECT App::DipRectToPixels(const D2D1_RECT_F& rect) const {
    const float scale = Scale();
    RECT pixels{};
    pixels.left = static_cast<LONG>(std::floor(rect.left * scale));
    pixels.top = static_cast<LONG>(std::floor(rect.top * scale));
    pixels.right = static_cast<LONG>(std::ceil(rect.right * scale));
    pixels.bottom = static_cast<LONG>(std::ceil(rect.bottom * scale));
    return pixels;
}

void App::HandleMouseMove(int x, int y) {
    layoutCache_ = BuildLayout();
    const float px = static_cast<float>(x) / Scale();
    const float py = static_cast<float>(y) / Scale();
    bool needsAnimation = false;
    const bool themeHover = PointInRect(px, py, layoutCache_.themeButton);
    const bool closeHover = PointInRect(px, py, layoutCache_.closeButton);
    const bool minimizeHover = PointInRect(px, py, layoutCache_.minimizeButton);
    const bool exitHover = PointInRect(px, py, layoutCache_.exitButton);
    if (themeHover != themeButton_.hover) { themeButton_.hover = themeHover; needsAnimation = true; }
    if (closeHover != closeButton_.hover) { closeButton_.hover = closeHover; needsAnimation = true; }
    if (minimizeHover != minimizeButton_.hover) { minimizeButton_.hover = minimizeHover; needsAnimation = true; }
    if (exitHover != exitButton_.hover) { exitButton_.hover = exitHover; needsAnimation = true; }

    bool onDpiButton = false;
    bool onDpiValue = false;
    bool onSlider = false;
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        for (size_t i = 0; i < devices_.size() && i < layoutCache_.cards.size(); ++i) {
            UiDevice& device = devices_[i];
            const CardLayout& card = layoutCache_.cards[i];
            const bool hover = PointInRect(px, py, card.card);
            if (hover != (device.hover > 0.5f)) needsAnimation = true;
            const bool dpiCapable = device.config.vid == 0x5253 &&
                                    device.config.pid == 0x1021;
            const bool dpiHover = device.dpiExpanded &&
                                  PointInRect(px, py, card.dpiButton);
            const bool dpiValueHover = dpiCapable && device.dpiExpanded &&
                                       PointInRect(px, py, card.dpiValueBox);
            const bool sliderHover = device.dpiExpanded &&
                                     PointInRect(px, py, card.slider);
            if (dpiHover != device.dpiHover ||
                dpiValueHover != device.dpiValueHover ||
                sliderHover != device.sliderHover) {
                needsAnimation = true;
            }
            device.dpiHover = dpiHover;
            device.dpiValueHover = dpiValueHover;
            device.sliderHover = sliderHover;
            if (device.dpiExpanded && PointInRect(px, py, card.dpiButton)) {
                onDpiButton = true;
            }
            if (dpiValueHover) onDpiValue = true;
            if (device.dpiExpanded && (sliderHover || device.sliderDragging)) {
                onSlider = true;
            }
            if (device.sliderDragging && device.reading.dpi) {
                const float ratio = (px - card.slider.left) /
                                    std::max(1.0f, card.slider.right - card.slider.left);
                device.draftDpi = SliderToDpi(ratio);
            }
        }
    }
    TRACKMOUSEEVENT tracking{sizeof(tracking), TME_LEAVE, hwnd_, 0};
    TrackMouseEvent(&tracking);
    if (onSlider) SetCursor(LoadCursorW(nullptr, IDC_SIZEWE));
    else if (onDpiValue) SetCursor(LoadCursorW(nullptr, IDC_IBEAM));
    else if (onDpiButton || themeHover || closeHover || minimizeHover || exitHover) {
        SetCursor(LoadCursorW(nullptr, IDC_HAND));
    } else {
        SetCursor(LoadCursorW(nullptr, IDC_ARROW));
    }
    if (needsAnimation) BeginAnimation();
    Invalidate();
}

void App::HandleLeftButtonDown(int x, int y) {
    layoutCache_ = BuildLayout();
    const float px = static_cast<float>(x) / Scale();
    const float py = static_cast<float>(y) / Scale();
    if (PointInRect(px, py, layoutCache_.themeButton)) {
        darkTheme_ = !darkTheme_;
        SaveConfig();
        const int dark = darkTheme_ ? TRUE : FALSE;
        DwmSetWindowAttribute(hwnd_, 20, &dark, sizeof(dark));
        BeginAnimation();
        Invalidate();
        return;
    }
    if (PointInRect(px, py, layoutCache_.closeButton)) {
        HideToTray();
        return;
    }
    if (PointInRect(px, py, layoutCache_.minimizeButton)) {
        ShowWindow(hwnd_, SW_MINIMIZE);
        return;
    }
    if (PointInRect(px, py, layoutCache_.exitButton)) {
        ExitApplication();
        return;
    }

    int readDpiIndex = -1;
    int editDpiIndex = -1;
    bool beganSliderDrag = false;
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        for (size_t i = 0; i < devices_.size() && i < layoutCache_.cards.size(); ++i) {
            UiDevice& device = devices_[i];
            const CardLayout& card = layoutCache_.cards[i];
            const bool dpiCapable = device.config.vid == 0x5253 &&
                                    device.config.pid == 0x1021;
            if (dpiCapable && PointInRect(px, py, card.dpiButton)) {
                device.dpiExpanded = !device.dpiExpanded;
                device.dpiAnimation = 0.0f;
                if (device.dpiExpanded) {
                    device.draftDpi = device.reading.dpi
                        ? device.reading.dpi->CurrentDpi()
                        : device.draftDpi;
                    readDpiIndex = static_cast<int>(i);
                }
                break;
            }
            if (device.dpiExpanded && dpiCapable &&
                PointInRect(px, py, card.dpiValueBox)) {
                editDpiIndex = static_cast<int>(i);
                break;
            }
            if (device.dpiExpanded && dpiCapable && PointInRect(px, py, card.slider)) {
                device.sliderDragging = true;
                const float ratio = (px - card.slider.left) /
                                    std::max(1.0f, card.slider.right - card.slider.left);
                device.draftDpi = SliderToDpi(ratio);
                beganSliderDrag = true;
                break;
            }
        }
    }
    if (editDpiIndex >= 0) {
        StartDpiEdit(editDpiIndex);
        BeginAnimation();
        Invalidate();
        return;
    }
    if (beganSliderDrag) SetCapture(hwnd_);
    if (readDpiIndex >= 0) BeginDpiRead(readDpiIndex);
    if (readDpiIndex >= 0 || beganSliderDrag) {
        BeginAnimation();
        Invalidate();
    }
}

void App::HandleLeftButtonUp(int x, int y) {
    (void)x;
    (void)y;
    bool releaseCapture = false;
    int writeIndex = -1;
    int writeValue = 0;
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        for (size_t i = 0; i < devices_.size(); ++i) {
            UiDevice& device = devices_[i];
            if (!device.sliderDragging) continue;
            device.sliderDragging = false;
            releaseCapture = true;
            const int previous = device.reading.dpi
                ? device.reading.dpi->CurrentDpi() : 0;
            if (device.draftDpi != previous) {
                writeIndex = static_cast<int>(i);
                writeValue = device.draftDpi;
            }
        }
    }
    if (releaseCapture && GetCapture() == hwnd_) ReleaseCapture();
    if (writeIndex >= 0) BeginDpiWrite(writeIndex, writeValue);
    Invalidate();
}

void App::HandleRightClick(int x, int y) {
    layoutCache_ = BuildLayout();
    const float px = static_cast<float>(x) / Scale();
    const float py = static_cast<float>(y) / Scale();
    for (size_t i = 0; i < layoutCache_.cards.size(); ++i) {
        if (PointInRect(px, py, layoutCache_.cards[i].card)) {
            POINT screen{};
            GetCursorPos(&screen);
            ShowDeviceMenu(static_cast<int>(i), screen);
            return;
        }
    }
}

LRESULT App::HandleHitTest(POINT screenPoint) {
    POINT point = screenPoint;
    ScreenToClient(hwnd_, &point);
    RECT client{};
    GetClientRect(hwnd_, &client);
    const int border = std::max(5, static_cast<int>(std::lround(7.0f * Scale())));
    const bool left = point.x < border;
    const bool right = point.x >= client.right - border;
    const bool top = point.y < border;
    const bool bottom = point.y >= client.bottom - border;
    if (top && left) return HTTOPLEFT;
    if (top && right) return HTTOPRIGHT;
    if (bottom && left) return HTBOTTOMLEFT;
    if (bottom && right) return HTBOTTOMRIGHT;
    if (left) return HTLEFT;
    if (right) return HTRIGHT;
    if (top) return HTTOP;
    if (bottom) return HTBOTTOM;

    const float dipX = static_cast<float>(point.x) / Scale();
    const float dipY = static_cast<float>(point.y) / Scale();
    if (dipY >= 0.0f && dipY < 84.0f) {
        layoutCache_ = BuildLayout();
        if (!PointInRect(dipX, dipY, layoutCache_.themeButton) &&
            !PointInRect(dipX, dipY, layoutCache_.closeButton)) {
            return HTCAPTION;
        }
    }
    return HTCLIENT;
}

void App::OnDpiChanged(UINT dpi, const RECT* suggested) {
    dpi_ = dpi ? dpi : 96;
    if (suggested) {
        SetWindowPos(hwnd_, nullptr, suggested->left, suggested->top,
                     suggested->right - suggested->left,
                     suggested->bottom - suggested->top,
                     SWP_NOZORDER | SWP_NOACTIVATE);
    }
    renderTarget_.Reset();
    CreateRenderTarget();
    UpdateEditControl();
    Invalidate();
}

void App::BeginAnimation() {
    if (hwnd_) {
        SetTimer(hwnd_, kAnimationTimer, 16, nullptr);
    }
}

void App::Animate() {
    bool active = false;
    const auto approach = [&](float& value, float target, float speed) {
        const float delta = target - value;
        if (std::abs(delta) > 0.002f) {
            value += delta * speed;
            active = true;
        } else {
            value = target;
        }
    };
    approach(themeProgress_, darkTheme_ ? 1.0f : 0.0f, 0.18f);
    approach(contentAnimation_, 1.0f, 0.16f);
    approach(themeButton_.hoverAmount, themeButton_.hover ? 1.0f : 0.0f, 0.22f);
    approach(closeButton_.hoverAmount, closeButton_.hover ? 1.0f : 0.0f, 0.22f);
    approach(minimizeButton_.hoverAmount, minimizeButton_.hover ? 1.0f : 0.0f, 0.22f);
    approach(exitButton_.hoverAmount, exitButton_.hover ? 1.0f : 0.0f, 0.22f);
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        for (auto& device : devices_) {
            approach(device.hover, device.hover > 0.5f ? 1.0f : 0.0f, 0.22f);
            approach(device.dpiAnimation, device.dpiExpanded ? 1.0f : 0.0f, 0.20f);
        }
    }
    Invalidate();
    if (!active) KillTimer(hwnd_, kAnimationTimer);
}

void App::Invalidate() {
    if (hwnd_) InvalidateRect(hwnd_, nullptr, FALSE);
}

}  // namespace

namespace {

void App::Render() {
    if (!renderTarget_) {
        if (!CreateRenderTarget()) return;
    }
    const Palette palette = CurrentPalette();
    renderTarget_->BeginDraw();
    renderTarget_->SetTransform(D2D1::Matrix3x2F::Identity());
    renderTarget_->Clear(palette.background);
    layoutCache_ = BuildLayout();
    DrawBackground(layoutCache_, palette);
    DrawHeader(layoutCache_, palette);
    DrawSummary(layoutCache_, palette);
    renderTarget_->PushAxisAlignedClip(layoutCache_.viewport,
                                        D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
    for (size_t i = 0; i < devices_.size() && i < layoutCache_.cards.size(); ++i) {
        const auto& card = layoutCache_.cards[i];
        if (card.card.bottom < layoutCache_.viewport.top ||
            card.card.top > layoutCache_.viewport.bottom) continue;
        DrawDeviceCard(static_cast<int>(i), card, palette);
    }
    renderTarget_->PopAxisAlignedClip();

    if (layoutCache_.maxScroll > 0.5f) {
        const float viewportHeight = layoutCache_.viewport.bottom -
                                     layoutCache_.viewport.top;
        const float ratio = viewportHeight /
                            (viewportHeight + layoutCache_.maxScroll);
        const float thumbHeight = std::max(34.0f, viewportHeight * ratio);
        const float travel = viewportHeight - thumbHeight;
        const float position = layoutCache_.maxScroll > 0.0f
            ? scrollOffset_ / layoutCache_.maxScroll : 0.0f;
        const D2D1_RECT_F thumb = D2D1::RectF(
            layoutCache_.viewport.right - 3.0f,
            layoutCache_.viewport.top + travel * position,
            layoutCache_.viewport.right,
            layoutCache_.viewport.top + travel * position + thumbHeight);
        FillRounded(thumb, 2.0f, WithAlpha(palette.textFaint, 0.35f));
    }
    DrawFooter(layoutCache_, palette);

    const HRESULT result = renderTarget_->EndDraw();
    if (result == D2DERR_RECREATE_TARGET) {
        renderTarget_.Reset();
        brush_.Reset();
        CreateRenderTarget();
    }
}

void App::DrawBackground(const PanelLayout&, const Palette&) {
    if (!brush_ || !renderTarget_) return;
    brush_->SetColor(WithAlpha(Color(0xE9B9A8), themeProgress_ > 0.5f ? 0.015f : 0.07f));
    renderTarget_->FillEllipse(
        D2D1::Ellipse(D2D1::Point2F(660.0f, 70.0f), 130.0f, 130.0f), brush_.Get());
    brush_->SetColor(WithAlpha(Color(0xA9D6C2), themeProgress_ > 0.5f ? 0.012f : 0.06f));
    renderTarget_->FillEllipse(
        D2D1::Ellipse(D2D1::Point2F(72.0f, 720.0f), 150.0f, 150.0f), brush_.Get());
}

void App::DrawHeader(const PanelLayout& layout, const Palette& palette) {
    const D2D1_RECT_F logoRect = D2D1::RectF(20.0f, 20.0f, 76.0f, 76.0f);
    FillRounded(logoRect, 17.0f, palette.logo);
    text_.logo->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
    text_.logo->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
    DrawText(L"2.4G", text_.logo.Get(), logoRect, palette.surface);

    DrawText(L"我的外设", text_.title.Get(),
             D2D1::RectF(92.0f, 22.0f, 420.0f, 54.0f), palette.text);
    DrawText(L"电量与状态，一眼看懂", text_.subtitle.Get(),
             D2D1::RectF(94.0f, 55.0f, 420.0f, 78.0f), palette.textMuted);

    const auto drawTopButton = [&](const D2D1_RECT_F& rect, float hover) {
        const D2D1_COLOR_F fill = Mix(palette.background, palette.surfaceAlt, hover);
        FillRounded(rect, 12.0f, WithAlpha(fill, 0.96f));
        StrokeRounded(rect, 12.0f, WithAlpha(palette.border, 0.55f), 1.0f);
    };
    drawTopButton(layout.themeButton, themeButton_.hoverAmount);
    drawTopButton(layout.closeButton, closeButton_.hoverAmount);
    DrawThemeIcon(layout.themeButton, palette);
    DrawCloseIcon(layout.closeButton, palette);
}

void App::DrawSummary(const PanelLayout& layout, const Palette& palette) {
    FillRounded(layout.summary, 18.0f, WithAlpha(palette.surfaceAlt, 0.78f));
    StrokeRounded(layout.summary, 18.0f, WithAlpha(palette.border, 0.48f), 1.0f);
    int online = 0;
    for (const auto& device : devices_) if (device.reading.present) ++online;
    const int total = static_cast<int>(devices_.size());
    const D2D1_COLOR_F dotColor = total > 0 && online == total
        ? palette.success : (online > 0 ? palette.warning : palette.textFaint);
    if (brush_) {
        brush_->SetColor(dotColor);
        renderTarget_->FillEllipse(
            D2D1::Ellipse(D2D1::Point2F(layout.summary.left + 22.0f,
                                        (layout.summary.top + layout.summary.bottom) / 2.0f),
                          5.0f, 5.0f), brush_.Get());
    }
    std::wstring summary;
    if (total == 0) summary = L"尚未添加设备";
    else if (online == total) summary = std::to_wstring(total) + L" 台设备全部在线";
    else summary = std::to_wstring(online) + L" / " + std::to_wstring(total) + L" 台在线";
    text_.body->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
    DrawText(summary, text_.body.Get(),
             D2D1::RectF(layout.summary.left + 38.0f, layout.summary.top,
                         layout.summary.right - 160.0f, layout.summary.bottom),
             palette.text);
    text_.small->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
    text_.small->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_TRAILING);
    const std::wstring refresh = refreshing_ ? L"正在同步…" :
        (intervalSeconds_ % 60 == 0 ? L"每 " + std::to_wstring(intervalSeconds_ / 60) + L" 分钟自动刷新" : L"每 " + std::to_wstring(intervalSeconds_) + L" 秒自动刷新");
    DrawText(refresh, text_.small.Get(),
             D2D1::RectF(layout.summary.right - 210.0f, layout.summary.top,
                         layout.summary.right - 18.0f, layout.summary.bottom),
             palette.textMuted);
    text_.small->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
}

void App::DrawFooter(const PanelLayout& layout, const Palette& palette) {
    std::wstring updated;
    if (lastUpdated_.time_since_epoch().count() == 0) {
        updated = L"首次读取中…";
    } else {
        const std::time_t time = std::chrono::system_clock::to_time_t(lastUpdated_);
        std::tm local{};
        localtime_s(&local, &time);
        wchar_t buffer[64]{};
        swprintf_s(buffer, L"上次更新  %02d:%02d:%02d",
                   local.tm_hour, local.tm_min, local.tm_sec);
        updated = buffer;
    }
    text_.small->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
    DrawText(updated, text_.small.Get(),
             D2D1::RectF(layout.footer.left, layout.footer.top,
                         layout.footer.right - 190.0f, layout.footer.bottom),
             palette.textMuted);

    const auto drawButton = [&](const D2D1_RECT_F& rect, const std::wstring& label,
                                float hover, bool primary) {
        const D2D1_COLOR_F fill = primary
            ? Mix(palette.logo, palette.textMuted, hover * 0.16f)
            : Mix(palette.surfaceAlt, palette.border, hover * 0.7f);
        FillRounded(rect, 11.0f, fill);
        if (!primary) StrokeRounded(rect, 11.0f, WithAlpha(palette.border, 0.6f), 1.0f);
        text_.button->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
        text_.button->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
        DrawText(label, text_.button.Get(), rect, primary ? palette.surface : palette.text);
    };
    drawButton(layout.minimizeButton, L"最小化", minimizeButton_.hoverAmount, false);
    drawButton(layout.exitButton, L"退出", exitButton_.hoverAmount, true);
}

void App::DrawThemeIcon(const D2D1_RECT_F& rect, const Palette& palette) {
    if (!brush_) return;
    const D2D1_POINT_2F center = D2D1::Point2F(
        (rect.left + rect.right) * 0.5f, (rect.top + rect.bottom) * 0.5f);
    brush_->SetColor(palette.textMuted);
    if (themeProgress_ < 0.5f) {
        renderTarget_->DrawEllipse(D2D1::Ellipse(center, 7.0f, 7.0f), brush_.Get(), 1.8f);
        for (int i = 0; i < 8; ++i) {
            const float angle = static_cast<float>(i) * 3.14159265f / 4.0f;
            const float dx = std::cos(angle);
            const float dy = std::sin(angle);
            renderTarget_->DrawLine(
                D2D1::Point2F(center.x + dx * 10.5f, center.y + dy * 10.5f),
                D2D1::Point2F(center.x + dx * 13.5f, center.y + dy * 13.5f),
                brush_.Get(), 1.5f, roundStroke_.Get());
        }
    } else {
        ComPtr<ID2D1PathGeometry> moon;
        if (SUCCEEDED(d2dFactory_->CreatePathGeometry(moon.ReleaseAndGetAddressOf()))) {
            ComPtr<ID2D1GeometrySink> sink;
            if (SUCCEEDED(moon->Open(sink.ReleaseAndGetAddressOf()))) {
                sink->BeginFigure(D2D1::Point2F(center.x + 7.5f, center.y - 11.0f),
                                  D2D1_FIGURE_BEGIN_FILLED);
                sink->AddArc(D2D1::ArcSegment(
                    D2D1::Point2F(center.x + 7.5f, center.y + 11.0f),
                    D2D1::SizeF(13.0f, 13.0f), 0.0f, D2D1_SWEEP_DIRECTION_CLOCKWISE,
                    D2D1_ARC_SIZE_SMALL));
                sink->AddArc(D2D1::ArcSegment(
                    D2D1::Point2F(center.x + 7.5f, center.y - 11.0f),
                    D2D1::SizeF(10.0f, 10.0f), 0.0f, D2D1_SWEEP_DIRECTION_COUNTER_CLOCKWISE,
                    D2D1_ARC_SIZE_SMALL));
                sink->EndFigure(D2D1_FIGURE_END_CLOSED);
                sink->Close();
                renderTarget_->FillGeometry(moon.Get(), brush_.Get());
            }
        }
    }
}

void App::DrawCloseIcon(const D2D1_RECT_F& rect, const Palette& palette) {
    if (!brush_) return;
    const float cx = (rect.left + rect.right) * 0.5f;
    const float cy = (rect.top + rect.bottom) * 0.5f;
    brush_->SetColor(palette.textMuted);
    renderTarget_->DrawLine(D2D1::Point2F(cx - 5.0f, cy - 5.0f),
                            D2D1::Point2F(cx + 5.0f, cy + 5.0f),
                            brush_.Get(), 1.6f, roundStroke_.Get());
    renderTarget_->DrawLine(D2D1::Point2F(cx + 5.0f, cy - 5.0f),
                            D2D1::Point2F(cx - 5.0f, cy + 5.0f),
                            brush_.Get(), 1.6f, roundStroke_.Get());
}

void App::DrawMinimizeIcon(const D2D1_RECT_F& rect, const Palette& palette) {
    if (!brush_) return;
    const float cx = (rect.left + rect.right) * 0.5f;
    const float cy = (rect.top + rect.bottom) * 0.5f;
    brush_->SetColor(palette.textMuted);
    renderTarget_->DrawLine(D2D1::Point2F(cx - 6.0f, cy + 3.0f),
                            D2D1::Point2F(cx + 6.0f, cy + 3.0f),
                            brush_.Get(), 1.6f, roundStroke_.Get());
}

void App::DrawShadow(const D2D1_RECT_F& rect, float radius, float alpha) {
    if (!brush_) return;
    brush_->SetColor(D2D1::ColorF(D2D1::ColorF::Black, alpha));
    for (int i = 3; i >= 1; --i) {
        const float offset = static_cast<float>(i) * 1.5f;
        const D2D1_RECT_F shadow = D2D1::RectF(
            rect.left - offset, rect.top - offset + 2.0f,
            rect.right + offset, rect.bottom + offset + 2.0f);
        renderTarget_->DrawRoundedRectangle(
            D2D1::RoundedRect(shadow, radius + offset, radius + offset),
            brush_.Get(), 0.7f);
    }
}

void App::DrawText(const std::wstring& text, IDWriteTextFormat* format,
                   const D2D1_RECT_F& rect, D2D1_COLOR_F color,
                   D2D1_DRAW_TEXT_OPTIONS options) {
    if (!renderTarget_ || !brush_ || !format || text.empty()) return;
    brush_->SetColor(color);
    renderTarget_->DrawTextW(text.c_str(), static_cast<UINT32>(text.size()), format,
                             rect, brush_.Get(), options,
                             DWRITE_MEASURING_MODE_NATURAL);
}

void App::FillRounded(const D2D1_RECT_F& rect, float radius, D2D1_COLOR_F color) {
    if (!renderTarget_ || !brush_) return;
    brush_->SetColor(color);
    renderTarget_->FillRoundedRectangle(D2D1::RoundedRect(rect, radius, radius),
                                        brush_.Get());
}

void App::StrokeRounded(const D2D1_RECT_F& rect, float radius, D2D1_COLOR_F color,
                        float width) {
    if (!renderTarget_ || !brush_) return;
    brush_->SetColor(color);
    renderTarget_->DrawRoundedRectangle(D2D1::RoundedRect(rect, radius, radius),
                                        brush_.Get(), width);
}

}  // namespace

namespace {

void App::DrawDeviceCard(int index, const CardLayout& card, const Palette& palette) {
    if (index < 0 || index >= static_cast<int>(devices_.size())) return;
    const UiDevice& device = devices_[static_cast<size_t>(index)];
    const D2D1_COLOR_F accent = AccentFor(device.config.type);
    const D2D1_COLOR_F accentText = StatusColor(device, palette, accent);
    const D2D1_COLOR_F tinted = Mix(palette.surface, accent,
                                    themeProgress_ > 0.5f ? 0.10f : 0.055f);
    DrawShadow(card.card, 22.0f, 0.022f + device.hover * 0.012f);
    FillRounded(card.card, 22.0f, tinted);
    StrokeRounded(card.card, 22.0f,
                  WithAlpha(accent, 0.10f + device.hover * 0.10f), 1.0f);

    DrawDeviceIcon(card.icon, device.config.type, accent);

    text_.cardTitle->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
    text_.cardTitle->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
    DrawText(DisplayName(device), text_.cardTitle.Get(), card.name, palette.text);
    text_.tiny->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
    DrawText(DeviceSubtitle(device), text_.tiny.Get(), card.type, palette.textMuted);

    std::wstring statusText = BatteryStatusLabel(device.reading);
    if (device.reading.stale && device.reading.level >= 0) statusText = L"上次读数";
    if (statusText == L"休眠 / 无响应") statusText = L"休眠";
    if (statusText == L"无电量数据") statusText = L"无数据";
    if (statusText == L"未知") statusText = L"待连接";
    FillRounded(card.status, 9.0f, WithAlpha(accentText, 0.12f));
    text_.status->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
    text_.status->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
    DrawText(statusText, text_.status.Get(), card.status, accentText);

    const bool dpiCapable = device.config.vid == 0x5253 &&
                            device.config.pid == 0x1021;
    if (dpiCapable) {
        const D2D1_COLOR_F buttonFill = device.dpiExpanded
            ? WithAlpha(accent, 0.16f) : WithAlpha(palette.surface, 0.72f);
        FillRounded(card.dpiButton, 9.0f, buttonFill);
        StrokeRounded(card.dpiButton, 9.0f, WithAlpha(accent, 0.20f), 1.0f);
        const int dpiValue = device.reading.dpi
            ? device.reading.dpi->CurrentDpi() : device.draftDpi;
        const std::wstring label = L"DPI " + std::to_wstring(dpiValue) +
                                   (device.dpiExpanded ? L"  ⌃" : L"  ⌄");
        text_.button->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
        text_.button->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
        DrawText(label, text_.button.Get(), card.dpiButton, accent);
    }

    const float progress = device.reading.level >= 0
        ? static_cast<float>(std::clamp(device.reading.level, 0, 100)) / 100.0f : 0.0f;
    DrawProgressBar(card.progress, progress,
                    device.reading.stale ? WithAlpha(accentText, 0.62f) : accentText,
                    palette);
    text_.tiny->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
    text_.tiny->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_NEAR);
    DrawText(device.reading.stale ? L"当前显示上次有效读数" : L"低于 20% 时提醒",
             text_.tiny.Get(),
             D2D1::RectF(card.progress.left, card.progress.bottom + 4.0f,
                         card.progress.right, card.progress.bottom + 19.0f),
             palette.textFaint);

    DrawBatteryRing(card, device, palette, accentText);
    if (device.dpiExpanded && dpiCapable) {
        DrawDpiPanel(index, card, device, palette, accent);
    }
}

void App::DrawDeviceIcon(const D2D1_RECT_F& rect, DeviceType type,
                         D2D1_COLOR_F accent) {
    if (!brush_ || !renderTarget_) return;
    FillRounded(rect, 15.0f, WithAlpha(accent, 0.12f));
    StrokeRounded(rect, 15.0f, WithAlpha(accent, 0.16f), 1.0f);
    brush_->SetColor(accent);
    const float cx = (rect.left + rect.right) * 0.5f;
    const float cy = (rect.top + rect.bottom) * 0.5f;
    const float stroke = 1.8f;
    switch (type) {
    case DeviceType::Mouse:
        renderTarget_->DrawRoundedRectangle(
            D2D1::RoundedRect(D2D1::RectF(cx - 9.0f, cy - 14.0f,
                                         cx + 9.0f, cy + 15.0f), 8.5f, 8.5f),
            brush_.Get(), stroke);
        renderTarget_->DrawLine(D2D1::Point2F(cx, cy - 13.0f),
                                D2D1::Point2F(cx, cy - 3.0f),
                                brush_.Get(), 1.5f, roundStroke_.Get());
        break;
    case DeviceType::Keyboard:
        renderTarget_->DrawRoundedRectangle(
            D2D1::RoundedRect(D2D1::RectF(cx - 16.0f, cy - 10.0f,
                                         cx + 16.0f, cy + 11.0f), 3.0f, 3.0f),
            brush_.Get(), stroke);
        for (int row = 0; row < 2; ++row) {
            for (int col = 0; col < 5; ++col) {
                renderTarget_->FillEllipse(
                    D2D1::Ellipse(D2D1::Point2F(cx - 12.0f + col * 6.0f,
                                                cy - 5.0f + row * 6.0f), 1.1f, 1.1f),
                    brush_.Get());
            }
        }
        renderTarget_->DrawLine(D2D1::Point2F(cx - 6.0f, cy + 8.0f),
                                D2D1::Point2F(cx + 6.0f, cy + 8.0f),
                                brush_.Get(), 1.5f, roundStroke_.Get());
        break;
    case DeviceType::Headset:
        renderTarget_->DrawEllipse(
            D2D1::Ellipse(D2D1::Point2F(cx, cy), 14.0f, 14.0f),
            brush_.Get(), stroke);
        renderTarget_->FillRoundedRectangle(
            D2D1::RoundedRect(D2D1::RectF(rect.left + 9.0f, cy - 3.0f,
                                         rect.left + 16.0f, cy + 13.0f), 3.0f, 3.0f),
            brush_.Get());
        renderTarget_->FillRoundedRectangle(
            D2D1::RoundedRect(D2D1::RectF(rect.right - 16.0f, cy - 3.0f,
                                         rect.right - 9.0f, cy + 13.0f), 3.0f, 3.0f),
            brush_.Get());
        break;
    case DeviceType::Speaker:
        renderTarget_->DrawRoundedRectangle(
            D2D1::RoundedRect(D2D1::RectF(cx - 10.0f, cy - 15.0f,
                                         cx + 10.0f, cy + 14.0f), 4.0f, 4.0f),
            brush_.Get(), stroke);
        renderTarget_->DrawEllipse(
            D2D1::Ellipse(D2D1::Point2F(cx, cy + 5.0f), 5.0f, 5.0f),
            brush_.Get(), stroke);
        renderTarget_->FillEllipse(
            D2D1::Ellipse(D2D1::Point2F(cx, cy - 8.0f), 2.2f, 2.2f), brush_.Get());
        break;
    case DeviceType::Gamepad:
        renderTarget_->DrawRoundedRectangle(
            D2D1::RoundedRect(D2D1::RectF(cx - 17.0f, cy - 9.0f,
                                         cx + 17.0f, cy + 11.0f), 8.0f, 8.0f),
            brush_.Get(), stroke);
        renderTarget_->DrawLine(D2D1::Point2F(cx - 10.0f, cy),
                                D2D1::Point2F(cx - 4.0f, cy),
                                brush_.Get(), 1.5f, roundStroke_.Get());
        renderTarget_->DrawLine(D2D1::Point2F(cx - 7.0f, cy - 3.0f),
                                D2D1::Point2F(cx - 7.0f, cy + 3.0f),
                                brush_.Get(), 1.5f, roundStroke_.Get());
        renderTarget_->FillEllipse(
            D2D1::Ellipse(D2D1::Point2F(cx + 7.0f, cy - 2.0f), 1.6f, 1.6f),
            brush_.Get());
        renderTarget_->FillEllipse(
            D2D1::Ellipse(D2D1::Point2F(cx + 11.0f, cy + 2.0f), 1.6f, 1.6f),
            brush_.Get());
        break;
    case DeviceType::Receiver:
        renderTarget_->DrawRoundedRectangle(
            D2D1::RoundedRect(D2D1::RectF(cx - 7.0f, cy + 1.0f,
                                         cx + 7.0f, cy + 14.0f), 3.0f, 3.0f),
            brush_.Get(), stroke);
        renderTarget_->DrawRoundedRectangle(
            D2D1::RoundedRect(D2D1::RectF(cx - 3.0f, cy - 14.0f,
                                         cx + 3.0f, cy + 2.0f), 1.5f, 1.5f),
            brush_.Get(), stroke);
        renderTarget_->DrawLine(D2D1::Point2F(cx - 7.0f, cy + 7.0f),
                                D2D1::Point2F(cx + 7.0f, cy + 7.0f),
                                brush_.Get(), 1.5f, roundStroke_.Get());
        break;
    default:
        renderTarget_->DrawEllipse(
            D2D1::Ellipse(D2D1::Point2F(cx, cy), 13.0f, 13.0f),
            brush_.Get(), stroke);
        renderTarget_->FillEllipse(
            D2D1::Ellipse(D2D1::Point2F(cx, cy), 3.0f, 3.0f), brush_.Get());
        break;
    }
}

void App::DrawBatteryRing(const CardLayout& card, const UiDevice& device,
                          const Palette& palette, D2D1_COLOR_F accent) {
    if (!renderTarget_ || !brush_ || !d2dFactory_) return;
    const D2D1_ELLIPSE track = D2D1::Ellipse(card.ringCenter, card.ringRadius,
                                             card.ringRadius);
    brush_->SetColor(WithAlpha(palette.track, 0.92f));
    renderTarget_->DrawEllipse(track, brush_.Get(), 4.0f);
    const int level = std::clamp(device.reading.level, 0, 100);
    if (device.reading.level >= 0 && level > 0) {
        brush_->SetColor(accent);
        if (level >= 100) {
            renderTarget_->DrawEllipse(D2D1::Ellipse(
                card.ringCenter, card.ringRadius - 2.0f, card.ringRadius - 2.0f),
                brush_.Get(), 4.0f);
        } else {
            ComPtr<ID2D1PathGeometry> path;
            ComPtr<ID2D1GeometrySink> sink;
            if (SUCCEEDED(d2dFactory_->CreatePathGeometry(
                    path.ReleaseAndGetAddressOf())) &&
                SUCCEEDED(path->Open(sink.ReleaseAndGetAddressOf()))) {
                const float startAngle = -3.14159265f / 2.0f;
                const float endAngle = startAngle +
                    2.0f * 3.14159265f * static_cast<float>(level) / 100.0f;
                const float r = card.ringRadius - 2.0f;
                const D2D1_POINT_2F start = D2D1::Point2F(
                    card.ringCenter.x + std::cos(startAngle) * r,
                    card.ringCenter.y + std::sin(startAngle) * r);
                const D2D1_POINT_2F end = D2D1::Point2F(
                    card.ringCenter.x + std::cos(endAngle) * r,
                    card.ringCenter.y + std::sin(endAngle) * r);
                sink->BeginFigure(start, D2D1_FIGURE_BEGIN_FILLED);
                sink->AddArc(D2D1::ArcSegment(
                    end, D2D1::SizeF(r, r), 0.0f,
                    D2D1_SWEEP_DIRECTION_CLOCKWISE,
                    level > 50 ? D2D1_ARC_SIZE_LARGE : D2D1_ARC_SIZE_SMALL));
                sink->EndFigure(D2D1_FIGURE_END_OPEN);
                sink->Close();
                renderTarget_->DrawGeometry(path.Get(), brush_.Get(), 4.0f,
                                            roundStroke_.Get());
            }
        }
    }
    text_.number->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
    text_.number->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
    const std::wstring value = device.reading.level >= 0
        ? std::to_wstring(device.reading.level) : L"--";
    DrawText(value, text_.number.Get(),
             D2D1::RectF(card.ringCenter.x - card.ringRadius,
                         card.ringCenter.y - 23.0f,
                         card.ringCenter.x + card.ringRadius,
                         card.ringCenter.y + 16.0f),
             accent);
    text_.tiny->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
    text_.tiny->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_NEAR);
    DrawText(device.reading.level >= 0 ? L"%" : L"暂无",
             text_.tiny.Get(),
             D2D1::RectF(card.ringCenter.x - card.ringRadius,
                         card.ringCenter.y + 15.0f,
                         card.ringCenter.x + card.ringRadius,
                         card.ringCenter.y + 31.0f),
             palette.textMuted);
    text_.tiny->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
}

void App::DrawProgressBar(const D2D1_RECT_F& rect, float progress,
                          D2D1_COLOR_F color, const Palette& palette) {
    FillRounded(rect, (rect.bottom - rect.top) * 0.5f, palette.track);
    progress = std::clamp(progress, 0.0f, 1.0f);
    if (progress > 0.0f) {
        const float width = std::max(8.0f, (rect.right - rect.left) * progress);
        const D2D1_RECT_F filled = D2D1::RectF(rect.left, rect.top,
                                               rect.left + width, rect.bottom);
        FillRounded(filled, (rect.bottom - rect.top) * 0.5f, color);
    }
}

void App::DrawDpiPanel(int index, const CardLayout& card, const UiDevice& device,
                       const Palette& palette, D2D1_COLOR_F accent) {
    FillRounded(card.dpiPanel, 14.0f, WithAlpha(palette.surface, 0.72f));
    StrokeRounded(card.dpiPanel, 14.0f, WithAlpha(accent, 0.13f), 1.0f);
    text_.small->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
    text_.small->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_NEAR);
    DrawText(L"DPI 灵敏度", text_.small.Get(),
             D2D1::RectF(card.dpiPanel.left + 20.0f, card.dpiPanel.top + 12.0f,
                         card.dpiPanel.right - 160.0f, card.dpiPanel.top + 32.0f),
             palette.textMuted);
    const bool editingThisDpi = dpiEditingIndex_ == index;
    FillRounded(card.dpiValueBox, 8.0f,
                WithAlpha(palette.surface, device.dpiValueHover ? 0.98f : 0.88f));
    StrokeRounded(card.dpiValueBox, 8.0f,
                  WithAlpha(accent, device.dpiValueHover ? 0.52f : 0.18f),
                  device.dpiValueHover ? 1.5f : 1.0f);
    if (!editingThisDpi) {
        text_.button->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
        text_.button->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
        DrawText(std::to_wstring(device.draftDpi), text_.button.Get(),
                 card.dpiValueBox, accent);
    }

    const float ratio = DpiToSlider(device.draftDpi);
    FillRounded(card.slider, 2.0f, palette.track);
    FillRounded(D2D1::RectF(card.slider.left, card.slider.top,
                            card.slider.left +
                                (card.slider.right - card.slider.left) * ratio,
                            card.slider.bottom), 2.0f, accent);
    if (brush_) {
        for (const int tick : kDpiVisibleTicks) {
            const float tickX = card.slider.left +
                (card.slider.right - card.slider.left) * DpiToSlider(tick);
            brush_->SetColor(tick <= device.draftDpi
                ? WithAlpha(palette.surface, 0.92f)
                : WithAlpha(accent, 0.36f));
            renderTarget_->FillEllipse(
                D2D1::Ellipse(D2D1::Point2F(tickX,
                    (card.slider.top + card.slider.bottom) * 0.5f), 2.2f, 2.2f),
                brush_.Get());
        }
    }
    const float knobX = card.slider.left +
        (card.slider.right - card.slider.left) * ratio;
    const float knobY = (card.slider.top + card.slider.bottom) * 0.5f;
    if (brush_) {
        brush_->SetColor(palette.surface);
        renderTarget_->FillEllipse(
            D2D1::Ellipse(D2D1::Point2F(knobX, knobY), 7.5f, 7.5f), brush_.Get());
        brush_->SetColor(accent);
        renderTarget_->DrawEllipse(
            D2D1::Ellipse(D2D1::Point2F(knobX, knobY), 7.5f, 7.5f),
            brush_.Get(), 2.2f);
    }
    const float sliderWidth = card.slider.right - card.slider.left;
    const bool compact = sliderWidth < 520.0f;
    text_.tiny->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
    text_.tiny->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_NEAR);
    if (!compact) {
        DrawText(dpiWorking_ ? L"正在与鼠标同步…" : L"拖动预览，松开后写入",
                 text_.tiny.Get(),
                 D2D1::RectF(card.dpiPanel.left + 160.0f,
                             card.dpiPanel.top + 12.0f,
                             card.dpiPanel.right - 170.0f,
                             card.dpiPanel.top + 32.0f),
                 palette.textFaint);
    }
    for (const int tick : kDpiVisibleTicks) {
        if (compact && tick != 100 && tick != 1600 && tick != 6400 && tick != 26000) {
            continue;
        }
        const float tickX = card.slider.left + sliderWidth * DpiToSlider(tick);
        float labelLeft = tickX - 30.0f;
        float labelRight = tickX + 30.0f;
        if (tick == 100) {
            text_.tiny->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
            labelLeft = card.slider.left;
            labelRight = labelLeft + 54.0f;
        } else if (tick == kDpiMax) {
            text_.tiny->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_TRAILING);
            labelLeft = card.slider.right - 64.0f;
            labelRight = card.slider.right;
        } else {
            text_.tiny->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
        }
        DrawText(std::to_wstring(tick), text_.tiny.Get(),
                 D2D1::RectF(labelLeft, card.slider.bottom + 4.0f,
                             labelRight, card.slider.bottom + 21.0f),
                 tick == device.draftDpi ? accent : palette.textFaint);
    }
    text_.tiny->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
    (void)index;
}

}  // namespace

namespace {

LRESULT App::HandleMessage(HWND hwnd, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
    case WM_NCCREATE: {
        auto* create = reinterpret_cast<CREATESTRUCTW*>(lParam);
        auto* app = static_cast<App*>(create->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(app));
        return TRUE;
    }

    case WM_NCCALCSIZE:
        if (wParam == TRUE) {
            if (IsZoomed(hwnd)) {
                auto* params = reinterpret_cast<NCCALCSIZE_PARAMS*>(lParam);
                MONITORINFO monitor{sizeof(monitor)};
                if (GetMonitorInfoW(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST),
                                    &monitor)) {
                    params->rgrc[0] = monitor.rcWork;
                }
            }
            return 0;
        }
        break;

    case WM_ERASEBKGND:
        return 1;

    case WM_PAINT: {
        PAINTSTRUCT paint{};
        BeginPaint(hwnd, &paint);
        Render();
        EndPaint(hwnd, &paint);
        return 0;
    }

    case WM_SIZE: {
        if (renderTarget_) {
            const D2D1_SIZE_U size = D2D1::SizeU(
                static_cast<UINT32>(std::max(1, GET_X_LPARAM(lParam))),
                static_cast<UINT32>(std::max(1, GET_Y_LPARAM(lParam))));
            if (FAILED(renderTarget_->Resize(size))) {
                renderTarget_.Reset();
                brush_.Reset();
                CreateRenderTarget();
            }
        }
        layoutCache_ = BuildLayout();
        scrollOffset_ = std::clamp(scrollOffset_, 0.0f, layoutCache_.maxScroll);
        UpdateEditControl();
        Invalidate();
        return 0;
    }

    case WM_DPICHANGED: {
        const UINT newDpi = LOWORD(wParam);
        OnDpiChanged(newDpi, reinterpret_cast<const RECT*>(lParam));
        return 0;
    }

    case WM_GETMINMAXINFO: {
        auto* info = reinterpret_cast<MINMAXINFO*>(lParam);
        UINT dpi = dpi_;
        if (!dpi) dpi = GetDpiForWindow(hwnd);
        if (!dpi) dpi = 96;
        info->ptMinTrackSize.x = MulDiv(440, static_cast<int>(dpi), 96);
        info->ptMinTrackSize.y = MulDiv(420, static_cast<int>(dpi), 96);
        return 0;
    }

    case WM_NCHITTEST:
        return HandleHitTest(POINT{GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)});

    case WM_EXITSIZEMOVE:
        SaveWindowSize();
        return 0;

    case WM_MOUSEMOVE: {
        const int x = GET_X_LPARAM(lParam);
        const int y = GET_Y_LPARAM(lParam);
        if (scrollbarDragging_) {
            layoutCache_ = BuildLayout();
            const float viewportHeight = layoutCache_.viewport.bottom -
                                         layoutCache_.viewport.top;
            const float ratio = viewportHeight /
                                (viewportHeight + layoutCache_.maxScroll);
            const float thumbHeight = std::max(34.0f, viewportHeight * ratio);
            const float travel = std::max(1.0f, viewportHeight - thumbHeight);
            const float delta = static_cast<float>(y) / Scale() - scrollbarDragStartY_;
            scrollOffset_ = std::clamp(scrollbarDragStartScroll_ +
                                       delta / travel * layoutCache_.maxScroll,
                                       0.0f, layoutCache_.maxScroll);
            Invalidate();
            return 0;
        }
        HandleMouseMove(x, y);
        return 0;
    }

    case WM_MOUSELEAVE:
        themeButton_.hover = false;
        closeButton_.hover = false;
        minimizeButton_.hover = false;
        exitButton_.hover = false;
        {
            std::lock_guard<std::mutex> lock(stateMutex_);
            for (auto& device : devices_) {
                device.hover = 0.0f;
                device.dpiHover = false;
                device.dpiValueHover = false;
                device.sliderHover = false;
            }
        }
        BeginAnimation();
        return 0;

    case WM_LBUTTONDOWN: {
        CommitActiveEdit();
        SetFocus(hwnd);
        const int x = GET_X_LPARAM(lParam);
        const int y = GET_Y_LPARAM(lParam);
        const float px = static_cast<float>(x) / Scale();
        const float py = static_cast<float>(y) / Scale();
        layoutCache_ = BuildLayout();
        if (layoutCache_.maxScroll > 0.5f &&
            px >= layoutCache_.viewport.right - 10.0f &&
            py >= layoutCache_.viewport.top && py <= layoutCache_.viewport.bottom) {
            const float viewportHeight = layoutCache_.viewport.bottom -
                                         layoutCache_.viewport.top;
            const float ratio = viewportHeight /
                                (viewportHeight + layoutCache_.maxScroll);
            const float thumbHeight = std::max(34.0f, viewportHeight * ratio);
            const float travel = std::max(1.0f, viewportHeight - thumbHeight);
            const float thumbTop = layoutCache_.viewport.top +
                scrollOffset_ / layoutCache_.maxScroll * travel;
            if (py >= thumbTop && py <= thumbTop + thumbHeight) {
                scrollbarDragStartY_ = py;
                scrollbarDragStartScroll_ = scrollOffset_;
            } else {
                const float position = std::clamp(
                    (py - layoutCache_.viewport.top - thumbHeight * 0.5f) / travel,
                    0.0f, 1.0f);
                scrollOffset_ = position * layoutCache_.maxScroll;
                scrollbarDragStartY_ = py;
                scrollbarDragStartScroll_ = scrollOffset_;
            }
            scrollbarDragging_ = true;
            SetCapture(hwnd);
            Invalidate();
            return 0;
        }
        HandleLeftButtonDown(x, y);
        layoutCache_ = BuildLayout();
        scrollOffset_ = std::clamp(scrollOffset_, 0.0f, layoutCache_.maxScroll);
        return 0;
    }

    case WM_LBUTTONUP:
        if (scrollbarDragging_) {
            scrollbarDragging_ = false;
            if (GetCapture() == hwnd) ReleaseCapture();
            Invalidate();
            return 0;
        }
        HandleLeftButtonUp(GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam));
        return 0;

    case WM_LBUTTONDBLCLK:
        CommitActiveEdit();
        HandleDoubleClick(GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam));
        return 0;

    case WM_RBUTTONUP:
        CommitActiveEdit();
        HandleRightClick(GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam));
        return 0;

    case WM_CAPTURECHANGED:
        if (reinterpret_cast<HWND>(lParam) != hwnd) {
            scrollbarDragging_ = false;
            std::lock_guard<std::mutex> lock(stateMutex_);
            for (auto& device : devices_) device.sliderDragging = false;
        }
        return 0;

    case WM_MOUSEWHEEL: {
        layoutCache_ = BuildLayout();
        const int notches = GET_WHEEL_DELTA_WPARAM(wParam) / WHEEL_DELTA;
        scrollOffset_ = std::clamp(scrollOffset_ - static_cast<float>(notches) * 72.0f,
                                   0.0f, layoutCache_.maxScroll);
        Invalidate();
        return 0;
    }

    case WM_KEYDOWN:
        if (wParam == VK_ESCAPE && !editControl_) {
            HideToTray();
            return 0;
        }
        break;

    case WM_CTLCOLOREDIT: {
        if (reinterpret_cast<HWND>(lParam) == editControl_ && editBrush_) {
            const Palette palette = CurrentPalette();
            SetTextColor(reinterpret_cast<HDC>(wParam), RGB(
                static_cast<BYTE>(std::lround(palette.text.r * 255.0f)),
                static_cast<BYTE>(std::lround(palette.text.g * 255.0f)),
                static_cast<BYTE>(std::lround(palette.text.b * 255.0f))));
            SetBkColor(reinterpret_cast<HDC>(wParam), RGB(
                static_cast<BYTE>(std::lround(palette.surface.r * 255.0f)),
                static_cast<BYTE>(std::lround(palette.surface.g * 255.0f)),
                static_cast<BYTE>(std::lround(palette.surface.b * 255.0f))));
            return reinterpret_cast<LRESULT>(editBrush_);
        }
        break;
    }

    case WM_TIMER:
        if (wParam == kAnimationTimer) {
            Animate();
            return 0;
        }
        break;

    case WM_ACTIVATE:
        if (LOWORD(wParam) == WA_INACTIVE && editControl_) CommitActiveEdit();
        break;

    case WM_COMMAND:
        if (editControl_ && reinterpret_cast<HWND>(lParam) == editControl_ &&
            HIWORD(wParam) == EN_KILLFOCUS) {
            PostMessageW(hwnd, kEditCommit, 0, 0);
            return 0;
        }
        break;

    case WM_CLOSE:
        HideToTray();
        return 0;

    case WM_QUERYENDSESSION:
        SaveWindowSize();
        return TRUE;

    case WM_ENDSESSION:
        if (wParam) SaveWindowSize();
        return 0;

    case WM_POWERBROADCAST:
        if (wParam == PBT_APMRESUMEAUTOMATIC || wParam == PBT_APMRESUMESUSPEND) {
            RequestRefresh();
        }
        return TRUE;

    case WM_DESTROY:
        hwnd_ = nullptr;
        PostQuitMessage(0);
        return 0;

    case kRefreshDone:
        HandleRefreshResult(reinterpret_cast<RefreshResult*>(lParam));
        return 0;

    case kDpiDone:
        HandleDpiResult(reinterpret_cast<DpiResult*>(lParam));
        return 0;

    case kShowPanel:
        ShowPanel();
        return 0;

    case kExitApp:
        ExitApplication();
        return 0;

    case kEditCommit:
        CommitActiveEdit();
        return 0;

    case kEditCancel:
        if (dpiEditingIndex_ >= 0) CancelDpiEdit(); else CancelRename();
        return 0;

    case kTrayMessage: {
        const UINT event = LOWORD(lParam);
        if (event == WM_CONTEXTMENU || event == WM_RBUTTONUP) {
            POINT point{};
            if (HIWORD(lParam) != 0) {
                point.x = GET_X_LPARAM(wParam);
                point.y = GET_Y_LPARAM(wParam);
            } else {
                GetCursorPos(&point);
            }
            ShowTrayMenu(point);
            return 0;
        }
        if (event == WM_LBUTTONUP || event == WM_LBUTTONDBLCLK ||
            event == NIN_SELECT || event == NIN_KEYSELECT) {
            ShowPanel();
            return 0;
        }
        return 0;
    }

    default:
        break;
    }

    static const UINT taskbarCreated = RegisterWindowMessageW(L"TaskbarCreated");
    if (message == taskbarCreated) {
        trayAdded_ = false;
        AddTrayIcon();
        UpdateTrayTooltip();
        return 0;
    }
    return DefWindowProcW(hwnd, message, wParam, lParam);
}

}  // namespace

namespace {

LRESULT CALLBACK WindowProcedure(HWND hwnd, UINT message, WPARAM wParam, LPARAM lParam) {
    App* app = App::FromWindow(hwnd);
    if (message == WM_NCCREATE && !app) {
        auto* create = reinterpret_cast<CREATESTRUCTW*>(lParam);
        app = static_cast<App*>(create->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(app));
    }
    if (app) return app->HandleMessage(hwnd, message, wParam, lParam);
    return DefWindowProcW(hwnd, message, wParam, lParam);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&controls);

    const HRESULT comResult = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    const bool comInitialized = SUCCEEDED(comResult);

    bool showOnLaunch = true;
    int argc = 0;
    if (PWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc)) {
        for (int i = 1; i < argc; ++i) {
            if (_wcsicmp(argv[i], L"--hidden") == 0) {
                showOnLaunch = false;
                break;
            }
        }
        LocalFree(argv);
    }

    int exitCode = 0;
    {
        App app(instance, showOnLaunch);
        if (app.Initialize()) exitCode = app.Run();
    }

    if (comInitialized) CoUninitialize();
    return exitCode;
}
