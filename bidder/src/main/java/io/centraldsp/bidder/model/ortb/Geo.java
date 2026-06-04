package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * OpenRTB 2.5 §3.2.19 — Geo object.
 * Embedded in Device and User.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class Geo {

    /** Latitude (decimal degrees, North is positive). */
    @JsonProperty("lat")
    private Double lat;

    /** Longitude (decimal degrees, East is positive). */
    @JsonProperty("lon")
    private Double lon;

    /**
     * Source of location data.
     * 1 = GPS/Location Services, 2 = IP Address, 3 = User Provided.
     */
    @JsonProperty("type")
    private Integer type;

    /** Country code using ISO-3166-1-alpha-3. */
    @JsonProperty("country")
    private String country;

    /** Region code using ISO-3166-2 (e.g. "NY" for New York). */
    @JsonProperty("region")
    private String region;

    /** City name (Google metro code). */
    @JsonProperty("city")
    private String city;

    /** Zip/postal code. */
    @JsonProperty("zip")
    private String zip;

    /** UTC offset of the Geo location in minutes. */
    @JsonProperty("utcoffset")
    private Integer utcOffset;
}
