package io.centraldsp.bidder.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Spring MVC web configuration.
 *
 * <p>Configures CORS so SSP integration testers and monitoring tools
 * can call /health and /bidder/ortb from browser-based dashboards.</p>
 */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Value("${cors.enabled:true}")
    private boolean corsEnabled;

    @Value("${cors.allowed.origins:*}")
    private String allowedOrigins;

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        if (!corsEnabled) return;

        registry.addMapping("/**")
                .allowedOriginPatterns(allowedOrigins)
                .allowedMethods("GET", "POST", "OPTIONS")
                .allowedHeaders("*")
                // Don't allow credentials with wildcard origin (security).
                // SSPs authenticate via token in query params, not cookies.
                .allowCredentials(false)
                .maxAge(3600);
    }
}
