cmake_minimum_required(VERSION 3.20)

project(EZ24GBatteryTray VERSION 2.0.0 LANGUAGES CXX RC)

if(MSVC)
    # 静态链接 VC 运行库，生成可直接运行的单文件 exe。
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

add_executable(EZ24GBatteryTray WIN32
    native/app.cpp
    native/devices.cpp
    native/devices.hpp
    native/json.hpp
    native/app.rc
)

target_include_directories(EZ24GBatteryTray PRIVATE native)
target_compile_features(EZ24GBatteryTray PRIVATE cxx_std_20)
target_compile_definitions(EZ24GBatteryTray PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
)

if(MSVC)
    target_compile_options(EZ24GBatteryTray PRIVATE /W4 /permissive- /utf-8 /EHsc)
    # app.rc 已嵌入自定义 manifest，避免链接器重复生成。
    target_link_options(EZ24GBatteryTray PRIVATE /MANIFEST:NO)
endif()

target_link_libraries(EZ24GBatteryTray PRIVATE
    d2d1
    dwrite
    dwmapi
    hid
    setupapi
    shell32
    shlwapi
    comctl32
    ole32
    user32
    gdi32
    advapi32
)

set_target_properties(EZ24GBatteryTray PROPERTIES
    OUTPUT_NAME "2.4G外设电量"
)
