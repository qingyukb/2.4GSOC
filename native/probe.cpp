#define NOMINMAX
#include "devices.hpp"
#include <cstdio>
#include <locale.h>

static void Print(const Reading& r) {
    wprintf(L"name=%ls present=%d ok=%d level=%d status=%ls\n", r.name.c_str(),
            r.present, r.ok, r.level, BatteryStatusLabel(r).c_str());
    if (!r.detail.empty()) wprintf(L"detail=%ls\n", r.detail.c_str());
    if (r.dpi) wprintf(L"dpi=%u index=%d\n", r.dpi->CurrentDpi(), r.dpi->currentIndex);
}

int wmain() {
    _wsetlocale(LC_ALL, L"");
    const auto ifaces = EnumerateHid();
    wprintf(L"HID interfaces: %zu\n", ifaces.size());
    const DeviceConfig devices[] = {
        {0x5253, 0x1021, L"MCHOSE A7 Pro", DeviceType::Mouse},
        {0x320F, 0x5088, L"VGN V98 PRO", DeviceType::Keyboard},
        {0x045E, 0x02D1, L"Xbox One", DeviceType::Gamepad},
    };
    for (const auto& d : devices) {
        wprintf(L"\n--- %04X:%04X ---\n", d.vid, d.pid);
        auto r = ReadDevice(d, ifaces, true);
        Print(r);
    }
    return 0;
}