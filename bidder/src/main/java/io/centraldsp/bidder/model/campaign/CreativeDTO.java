package io.centraldsp.bidder.model.campaign;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class CreativeDTO {

    @JsonProperty("id")
    private Long id;

    @JsonProperty("format")
    private String format;  // banner, native, video

    @JsonProperty("width")
    private Integer width;

    @JsonProperty("height")
    private Integer height;

    @JsonProperty("image_url")
    private String imageUrl;

    @JsonProperty("title")
    private String title;

    @JsonProperty("description")
    private String description;

    @JsonProperty("click_url")
    private String clickUrl;

    @JsonProperty("vast_url")
    private String vastUrl;  // for video creatives

    @JsonProperty("adomain")
    private String adomain;

    @JsonProperty("status")
    private String status;  // approved, pending, rejected
}
