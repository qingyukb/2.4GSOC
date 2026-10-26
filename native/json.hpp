#pragma once

#include <windows.h>

#include <cmath>
#include <cwchar>
#include <cwctype>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace mini_json {

struct Value {
    enum class Type { Null, Bool, Number, String, Array, Object };

    Type type = Type::Null;
    bool boolean = false;
    double number = 0.0;
    std::wstring string;
    std::vector<Value> array;
    std::vector<std::pair<std::wstring, Value>> object;

    static Value MakeObject() {
        Value v;
        v.type = Type::Object;
        return v;
    }

    static Value MakeArray() {
        Value v;
        v.type = Type::Array;
        return v;
    }

    static Value MakeString(std::wstring text) {
        Value v;
        v.type = Type::String;
        v.string = std::move(text);
        return v;
    }

    static Value MakeNumber(double n) {
        Value v;
        v.type = Type::Number;
        v.number = n;
        return v;
    }

    static Value MakeBool(bool b) {
        Value v;
        v.type = Type::Bool;
        v.boolean = b;
        return v;
    }

    bool IsObject() const { return type == Type::Object; }
    bool IsArray() const { return type == Type::Array; }
    bool IsString() const { return type == Type::String; }
    bool IsNumber() const { return type == Type::Number; }

    Value* Find(const std::wstring& key) {
        if (!IsObject()) return nullptr;
        for (auto& item : object) {
            if (item.first == key) return &item.second;
        }
        return nullptr;
    }

    const Value* Find(const std::wstring& key) const {
        if (!IsObject()) return nullptr;
        for (const auto& item : object) {
            if (item.first == key) return &item.second;
        }
        return nullptr;
    }

    Value& operator[](const std::wstring& key) {
        if (!IsObject()) {
            type = Type::Object;
            array.clear();
            string.clear();
        }
        for (auto& item : object) {
            if (item.first == key) return item.second;
        }
        object.emplace_back(key, Value{});
        return object.back().second;
    }

    void Set(const std::wstring& key, Value value) {
        if (!IsObject()) {
            type = Type::Object;
            array.clear();
        }
        for (auto& item : object) {
            if (item.first == key) {
                item.second = std::move(value);
                return;
            }
        }
        object.emplace_back(key, std::move(value));
    }

    void Erase(const std::wstring& key) {
        if (!IsObject()) return;
        for (auto it = object.begin(); it != object.end(); ++it) {
            if (it->first == key) {
                object.erase(it);
                return;
            }
        }
    }

    std::wstring AsString(const std::wstring& fallback = L"") const {
        return type == Type::String ? string : fallback;
    }

    double AsNumber(double fallback = 0.0) const {
        return type == Type::Number ? number : fallback;
    }

    bool AsBool(bool fallback = false) const {
        return type == Type::Bool ? boolean : fallback;
    }
};

class Parser {
public:
    explicit Parser(std::wstring_view source) : source_(source) {}

    Value Parse() {
        SkipWs();
        Value value = ParseValue();
        SkipWs();
        if (pos_ != source_.size()) {
            throw std::runtime_error("JSON contains trailing characters");
        }
        return value;
    }

private:
    Value ParseValue() {
        SkipWs();
        if (pos_ >= source_.size()) throw std::runtime_error("Unexpected end of JSON");
        const wchar_t ch = source_[pos_];
        switch (ch) {
        case L'{': return ParseObject();
        case L'[': return ParseArray();
        case L'"': {
            Value value;
            value.type = Value::Type::String;
            value.string = ParseString();
            return value;
        }
        case L't':
            ExpectLiteral(L"true");
            return Value::MakeBool(true);
        case L'f':
            ExpectLiteral(L"false");
            return Value::MakeBool(false);
        case L'n':
            ExpectLiteral(L"null");
            return Value{};
        default:
            if (ch == L'-' || (ch >= L'0' && ch <= L'9')) return ParseNumber();
            throw std::runtime_error("Invalid JSON value");
        }
    }

    Value ParseObject() {
        Consume(L'{');
        Value obj = Value::MakeObject();
        SkipWs();
        if (TryConsume(L'}')) return obj;
        while (true) {
            SkipWs();
            if (pos_ >= source_.size() || source_[pos_] != L'"') {
                throw std::runtime_error("Expected JSON object key");
            }
            std::wstring key = ParseString();
            SkipWs();
            Consume(L':');
            obj.object.emplace_back(std::move(key), ParseValue());
            SkipWs();
            if (TryConsume(L'}')) break;
            Consume(L',');
        }
        return obj;
    }

    Value ParseArray() {
        Consume(L'[');
        Value arr = Value::MakeArray();
        SkipWs();
        if (TryConsume(L']')) return arr;
        while (true) {
            arr.array.push_back(ParseValue());
            SkipWs();
            if (TryConsume(L']')) break;
            Consume(L',');
        }
        return arr;
    }

    std::wstring ParseString() {
        Consume(L'"');
        std::wstring out;
        while (pos_ < source_.size()) {
            wchar_t ch = source_[pos_++];
            if (ch == L'"') return out;
            if (ch != L'\\') {
                if (ch < 0x20) throw std::runtime_error("Control character in JSON string");
                out.push_back(ch);
                continue;
            }
            if (pos_ >= source_.size()) throw std::runtime_error("Incomplete JSON escape");
            ch = source_[pos_++];
            switch (ch) {
            case L'"': out.push_back(L'"'); break;
            case L'\\': out.push_back(L'\\'); break;
            case L'/': out.push_back(L'/'); break;
            case L'b': out.push_back(L'\b'); break;
            case L'f': out.push_back(L'\f'); break;
            case L'n': out.push_back(L'\n'); break;
            case L'r': out.push_back(L'\r'); break;
            case L't': out.push_back(L'\t'); break;
            case L'u': out.push_back(ParseHex4()); break;
            default: throw std::runtime_error("Invalid JSON escape");
            }
        }
        throw std::runtime_error("Unterminated JSON string");
    }

    wchar_t ParseHex4() {
        if (pos_ + 4 > source_.size()) throw std::runtime_error("Incomplete unicode escape");
        unsigned value = 0;
        for (int i = 0; i < 4; ++i) {
            wchar_t ch = source_[pos_++];
            value <<= 4;
            if (ch >= L'0' && ch <= L'9') value |= static_cast<unsigned>(ch - L'0');
            else if (ch >= L'a' && ch <= L'f') value |= static_cast<unsigned>(ch - L'a' + 10);
            else if (ch >= L'A' && ch <= L'F') value |= static_cast<unsigned>(ch - L'A' + 10);
            else throw std::runtime_error("Invalid unicode escape");
        }
        return static_cast<wchar_t>(value);
    }

    Value ParseNumber() {
        const size_t start = pos_;
        if (Peek() == L'-') ++pos_;
        while (pos_ < source_.size() && std::iswdigit(source_[pos_])) ++pos_;
        if (Peek() == L'.') {
            ++pos_;
            while (pos_ < source_.size() && std::iswdigit(source_[pos_])) ++pos_;
        }
        if (Peek() == L'e' || Peek() == L'E') {
            ++pos_;
            if (Peek() == L'+' || Peek() == L'-') ++pos_;
            while (pos_ < source_.size() && std::iswdigit(source_[pos_])) ++pos_;
        }
        std::wstring token(source_.substr(start, pos_ - start));
        wchar_t* end = nullptr;
        const double number = std::wcstod(token.c_str(), &end);
        if (!end || *end != L'\0') throw std::runtime_error("Invalid JSON number");
        return Value::MakeNumber(number);
    }

    void ExpectLiteral(std::wstring_view literal) {
        if (source_.substr(pos_, literal.size()) != literal) {
            throw std::runtime_error("Invalid JSON literal");
        }
        pos_ += literal.size();
    }

    void SkipWs() {
        while (pos_ < source_.size()) {
            wchar_t ch = source_[pos_];
            if (ch != L' ' && ch != L'\n' && ch != L'\r' && ch != L'\t') break;
            ++pos_;
        }
    }

    wchar_t Peek() const {
        return pos_ < source_.size() ? source_[pos_] : L'\0';
    }

    bool TryConsume(wchar_t ch) {
        if (Peek() != ch) return false;
        ++pos_;
        return true;
    }

    void Consume(wchar_t ch) {
        if (!TryConsume(ch)) throw std::runtime_error("Unexpected JSON token");
    }

    std::wstring_view source_;
    size_t pos_ = 0;
};

inline Value Parse(const std::wstring& source) {
    return Parser(source).Parse();
}

inline void DumpString(const std::wstring& text, std::wstring& out) {
    out.push_back(L'"');
    for (wchar_t ch : text) {
        switch (ch) {
        case L'"': out += L"\\\""; break;
        case L'\\': out += L"\\\\"; break;
        case L'\b': out += L"\\b"; break;
        case L'\f': out += L"\\f"; break;
        case L'\n': out += L"\\n"; break;
        case L'\r': out += L"\\r"; break;
        case L'\t': out += L"\\t"; break;
        default:
            if (ch < 0x20) {
                wchar_t buf[7] = {};
                swprintf_s(buf, L"\\u%04X", static_cast<unsigned>(ch));
                out += buf;
            } else {
                out.push_back(ch);
            }
            break;
        }
    }
    out.push_back(L'"');
}

inline void DumpValue(const Value& value, std::wstring& out, int indent, int depth) {
    auto appendIndent = [&](int level) {
        out.append(static_cast<size_t>(level * indent), L' ');
    };

    switch (value.type) {
    case Value::Type::Null:
        out += L"null";
        break;
    case Value::Type::Bool:
        out += value.boolean ? L"true" : L"false";
        break;
    case Value::Type::Number: {
        if (!std::isfinite(value.number)) {
            out += L"0";
        } else if (std::floor(value.number) == value.number &&
                   std::abs(value.number) < 1.0e15) {
            out += std::to_wstring(static_cast<long long>(value.number));
        } else {
            wchar_t buf[64] = {};
            swprintf_s(buf, L"%.15g", value.number);
            out += buf;
        }
        break;
    }
    case Value::Type::String:
        DumpString(value.string, out);
        break;
    case Value::Type::Array: {
        if (value.array.empty()) {
            out += L"[]";
            break;
        }
        out += L"[\n";
        for (size_t i = 0; i < value.array.size(); ++i) {
            appendIndent(depth + 1);
            DumpValue(value.array[i], out, indent, depth + 1);
            if (i + 1 < value.array.size()) out.push_back(L',');
            out.push_back(L'\n');
        }
        appendIndent(depth);
        out.push_back(L']');
        break;
    }
    case Value::Type::Object: {
        if (value.object.empty()) {
            out += L"{}";
            break;
        }
        out += L"{\n";
        for (size_t i = 0; i < value.object.size(); ++i) {
            appendIndent(depth + 1);
            DumpString(value.object[i].first, out);
            out += L": ";
            DumpValue(value.object[i].second, out, indent, depth + 1);
            if (i + 1 < value.object.size()) out.push_back(L',');
            out.push_back(L'\n');
        }
        appendIndent(depth);
        out.push_back(L'}');
        break;
    }
    }
}

inline std::wstring Dump(const Value& value, int indent = 2) {
    std::wstring out;
    DumpValue(value, out, indent, 0);
    out.push_back(L'\n');
    return out;
}

}  // namespace mini_json