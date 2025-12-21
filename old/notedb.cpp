#include <iostream>
#include <fstream>
#include <sstream>
#include <map>
#include <vector>
#include <algorithm>
#include <bits/stdc++.h>
using namespace std;
map <string, bool> vis;
class SimpleDB {
private:
    std::string filename;
    std::map<std::string, std::string> data;

    void load() {
        std::ifstream file(filename);
        std::string line;
        while (std::getline(file, line)) {
            std::istringstream iss(line);
            std::string key, value;
            if (std::getline(iss, key, ':') && std::getline(iss, value)) {
                data[key] = value;
            }
        }
        file.close();
    }

    void save() {
        std::ofstream file(filename);
        for (const auto& pair : data) {
            file << pair.first << ":" << pair.second << "\n";
        }
        file.close();
    }

public:
    SimpleDB(const std::string& dbname) : filename(dbname + ".db") {
        load();
    }

    ~SimpleDB() {
        save();
    }

    void insert(const std::string& key, const std::string& value) {
        data[key] = value;
        std::cout << "Inserted: " << key << ":" << value << std::endl;
    }

    void update(const std::string& key, const std::string& value) {
        if (data.find(key) != data.end()) {
            data[key] = value;
            std::cout << "Updated: " << key << ":" << value << std::endl;
        } else {
            std::cout << "Key not found!" << std::endl;
        }
    }

    void remove(const std::string& key) {
        if (data.erase(key)) {
            std::cout << "Removed: " << key << std::endl;
        } else {
            std::cout << "Key not found!" << std::endl;
        }
    }

    std::string find(const std::string& key) {
        // if (data.find(key) != data.end()) {
        //     return data[key];
        // }
        string ans;
        for (const auto& pair : data) {
            // vis[pair.first] = 0;
            if (pair.first.find(key) == string::npos) continue;
            ans += pair.first + ":" + pair.second + "\n\n";
            // std::cout << pair.first << ":" << pair.second << std::endl << "\n";
        }
        if (ans.size()) return ans;
        return "Key not found!";
    }

    void printAll() {
        if (data.empty()) {
            std::cout << "Database is empty." << std::endl;
            return;
        }
        for (const auto& pair : data) {
            // vis[pair.first] = 0;
            std::cout << pair.first << ":" << pair.second << std::endl << "\n";
        }
        // for (const auto& pair : data) {
        //     if (vis[pair.first]) continue;
        //     vis[pair.first] = 1;
        // }
    }
    void printAllPY() {
        if (data.empty()) {
            std::cout << "Database is empty." << std::endl;
            return;
        }
        for (const auto& pair : data) {
            std::cout << pair.first << ":" << pair.second << "\"<br>\"";
        }
    }
};

void clearScreen() {
#ifdef _WIN32
    system("cls");
#else
    system("clear");
#endif
}

void clearCommandHistory() {
#ifdef _WIN32
    system("doskey /reinstall");  // Windows
#else
    system("history -c");  // Linux 和 macOS
    system("history -w");  // 可选：写入文件
#endif
}

void printUsage() {
    std::cout << "Usage: ./simple_db <command> [arguments]" << std::endl;
    std::cout << "Commands:" << std::endl;
    std::cout << "  insert <key> <value>  - Insert a new key-value pair" << std::endl;
    std::cout << "  update <key> <value>  - Update an existing key-value pair" << std::endl;
    std::cout << "  remove <key>          - Remove a key-value pair" << std::endl;
    std::cout << "  find <key>            - Find the value of a key" << std::endl;
    std::cout << "  list                  - List all key-value pairs" << std::endl;
    std::cout << "  clearcli              - clear the content of the CMD's screen\n";
    std::cout << "  clearhis              - Clear command history\n";
}
std::ofstream flog("log.txt");
int main(int argc, char* argv[]) {
    // flog << argc << "\n";
    if (argc < 2) {
        printUsage();
        return 1;
    }

    SimpleDB db("mydatabase");

    std::string command = argv[1];

    if (command == "insert" && argc == 4) {
        db.insert(argv[2], argv[3]);
    } else if (command == "update" && argc == 4) {
        db.update(argv[2], argv[3]);
    } else if (command == "remove" && argc == 3) {
        db.remove(argv[2]);
    } else if (command == "find" && argc == 3) {
        std::string value = db.find(argv[2]);
        std::cout  << value << std::endl;
    } else if (command == "list") {
        db.printAll();
    } else if (command == "pylist") {
        db.printAllPY();
    } else if (command == "clearcli") {
        clearScreen();
    } else if (command == "clearhis") {
        clearCommandHistory();
    } else {
        printUsage();
        return 1;
    }

    return 0;
}