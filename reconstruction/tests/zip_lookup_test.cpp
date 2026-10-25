#include "zip_lookup.hpp"
#include <cstdlib>
#include <iostream>
using namespace nova3;
static void require(bool value) { if (!value) { std::cerr << "ZIP lookup assertion failed\n"; std::exit(1); } }
int main() {
    require(zip_entry_key("Dir/FILE.BDAE", true, true) == "file.bdae");
    require(zip_entry_key("Dir\\FILE.BDAE", true, true) == "dir\\file.bdae");
    require(zip_query_key("Dir\\FILE.BDAE", true, true) == "file.bdae");
    for (const auto name : {"/foo", "\\foo", "/", "\\", ""})
        require(zip_query_key(name, false, true) == name);
    require(zip_entry_key("a/", false, true).empty());
    require(zip_query_key("a\\", false, true).empty());
    require(zip_entry_key("//foo", false, true) == "foo");
    require(zip_entry_key("dir/a", false, false) == "dir/a");
    const std::string high = std::string(1, static_cast<char>(0xc0)) + "AZ";
    require(zip_entry_key(high, true, false) == std::string(1, static_cast<char>(0xc0)) + "az");
    std::vector<ZipLookupEntry> entries;
    sort_zip_lookup(entries);require(find_zip_lookup(entries, "") == std::size_t(-1));
    entries={{"x",0},{"x",1}};sort_zip_lookup(entries);
    require(entries[0].archive_index==1 && entries[1].archive_index==0);
    require(find_zip_lookup(entries,"x")==1);
    entries={{"x",0},{"x",1},{"x",2}};sort_zip_lookup(entries);
    require(entries[0].archive_index==1 && entries[1].archive_index==2 && entries[2].archive_index==0);
    entries={{"b",0},{"a",1},{"b",2},{"a",3}};sort_zip_lookup(entries);
    const std::size_t expected[]={1,3,2,0};
    for(std::size_t i=0;i<4;++i)require(entries[i].archive_index==expected[i]);
    require(find_zip_lookup(entries,"a")==1 && find_zip_lookup(entries,"b")==2);
    require(find_zip_lookup(entries,"")==std::size_t(-1));
    require(find_zip_lookup(entries,"c")==std::size_t(-1));
    std::cout << "PASS: native ZIP keys, lower_bound, duplicate heapsort behavior\n";
}
