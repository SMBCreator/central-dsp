package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class App {
    @JsonProperty("id")     private String id;
    @JsonProperty("name")   private String name;
    @JsonProperty("bundle") private String bundle;
    @JsonProperty("domain") private String domain;
    @JsonProperty("cat")    private java.util.List<String> cat;
    @JsonProperty("storeurl") private String storeurl;
}
