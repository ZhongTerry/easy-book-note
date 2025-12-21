#include <bits/stdc++.h>

using namespace std;
/*
*/
#include "json/json.h"
ifstream fin("database.json");
void demo_parse_mem_object()
{
    char json_data[1005];
    fin >> json_data; 

    Json::Reader reader;
    Json::Value root;
    // reader将Json字符串解析到root，root将包含Json里所有子元素  
    if (!reader.parse(json_data, json_data + sizeof(json_data), root))
    {
        cerr << "json parse failed\n";
        return;
    }
    
    cout << "demo read from memory ---------\n";
    string name = root["name"].asString();
    int salary = root["salary"].asInt();
    string msg = root["msg"].asString();
    cout << "name: " << name << " salary: " << salary;
    cout << " msg: " << msg << endl;
    cout << "enter files: \n";
    Json::Value files = root["files"]; // read array here
    for (unsigned int i = 0; i < files.size(); ++i)
    {
        cout << files[i].asString() << " ";
    }
    cout << endl << endl;
}
int main() {
    ios::sync_with_stdio(false); cin.tie(0); cout.tie(0);
    demo_parse_mem_object();
    return 0;
}