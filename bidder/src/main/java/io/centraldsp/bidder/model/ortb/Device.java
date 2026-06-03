package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class Device {
    @JsonProperty("ua")       private String ua;
    @JsonProperty("ip")       private String ip;
    @JsonProperty("ipv6")     private String ipv6;
    @JsonProperty("geo")      private Geo geo;
    @JsonProperty("devicetype") private Integer devicetype;
    @JsonProperty("os")       private String os;
    @JsonProperty("osv")      private String osv;
    @JsonProperty("browser")  private String browser;
    @JsonProperty("language") private String language;
    @JsonProperty("make")     private String make;
    @JsonProperty("model")    private String model;
    @JsonProperty("connectiontype") private Integer connectiontype;
    @JsonProperty("ifa")      private String ifa;

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Geo {
        @JsonProperty("lat")     private Double lat;
        @JsonProperty("lon")     private Double lon;
        @JsonProperty("country") private String country;
        @JsonProperty("region")  private String region;
        @JsonProperty("city")    private String city;
        @JsonProperty("zip")     private String zip;
    }
}
