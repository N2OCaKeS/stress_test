#include <stdio.h>
#include <stdlib.h>
//#include <string.h>
//sudo apt install libcurl4-openssl-dev -y
#include <curl/curl.h>


//void mess(char message[]) {
//    printf("%s\n", message);
//    system(message);
//}


int download_file(const char* url, const char* name_file) {

    CURL *curl;
    FILE *fp;
    int result;

    curl = curl_easy_init();

    if (curl) {
        fp = fopen(name_file, "wb"); //file name to save path

        curl_easy_setopt(curl, CURLOPT_URL, url);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, NULL);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, fp);

        result = curl_easy_perform(curl);

        if (result != CURLE_OK)
            fprintf(stderr, "curl_easy_perform() failed: %s\n",
                    curl_easy_strerror(result));

        curl_easy_cleanup(curl);
        fclose(fp);
    }

    return result;
}
