package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class Site {
    @JsonProperty("id")     private String id;
    @JsonProperty("domain") private String domain;
    @JsonProperty("page")   private String page;
    @JsonProperty("cat")    private java.util.List<String> cat;
    @JsonProperty("publisher") private Publisher publisher;

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Publisher {
        @JsonProperty("id")   private String id;
        @JsonProperty("name") private String name;
    }
}
