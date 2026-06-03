package io.centraldsp.bidder.service;

import io.centraldsp.bidder.model.campaign.CampaignDTO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

import javax.annotation.PostConstruct;
import java.time.LocalDate;

/**
 * Budget enforcement using Redis counters.
 * Uses a daily key pattern: cdsp:budget:{campaignId}:{YYYY-MM-DD}
 * TTL = 25 hours (covers timezone overlap safely).
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class BudgetService {

    @Value("${redis.host:redis}")
    private String redisHost;

    @Value("${redis.port:6379}")
    private int redisPort;

    @Value("${redis.db:1}")
    private int redisDb;

    private JedisPool jedisPool;

    private static final String KEY_PREFIX = "cdsp:budget:";
    private static final long DAILY_TTL_SECONDS = 90_000L; // 25 hours

    @PostConstruct
    public void init() {
        JedisPoolConfig config = new JedisPoolConfig();
        config.setMaxTotal(20);
        config.setMaxIdle(5);
        config.setMinIdle(1);
        jedisPool = new JedisPool(config, redisHost, redisPort);
        log.info("[BudgetService] Connected to Redis {}:{}", redisHost, redisPort);
    }

    /** Initialize daily budget counter if not exists. */
    public void initBudget(CampaignDTO campaign) {
        String key = dailyKey(campaign.getId());
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            // Set only if key doesn't exist (SETNX)
            Long result = jedis.setnx(key, "0");
            if (result == 1) {
                jedis.expire(key, DAILY_TTL_SECONDS);
            }
        } catch (Exception e) {
            log.error("[BudgetService] initBudget failed for campaign {}: {}",
                    campaign.getId(), e.getMessage());
        }
    }

    /**
     * Check if campaign has remaining daily budget.
     * @param campaign Campaign to check
     * @param bidPrice Proposed bid price to deduct
     * @return true if budget available
     */
    public boolean hasBudget(CampaignDTO campaign, double bidPrice) {
        if (campaign.getDailyBudget() == null) return true;
        String key = dailyKey(campaign.getId());
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            String val = jedis.get(key);
            double spent = val != null ? Double.parseDouble(val) : 0.0;
            return (spent + bidPrice) <= campaign.getDailyBudget();
        } catch (Exception e) {
            log.warn("[BudgetService] hasBudget check error: {}", e.getMessage());
            return true; // fail-open on Redis errors
        }
    }

    /**
     * Atomically increment daily spend (called on win notice).
     * @param campaignId Campaign ID
     * @param winPrice   Actual clearing price (CPM)
     */
    public void recordSpend(Long campaignId, double winPrice) {
        String key = dailyKey(campaignId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            jedis.incrbyfloat(key, winPrice);
        } catch (Exception e) {
            log.error("[BudgetService] recordSpend failed: {}", e.getMessage());
        }
    }

    /** Get today's spend for a campaign. */
    public double getDailySpend(Long campaignId) {
        String key = dailyKey(campaignId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            String val = jedis.get(key);
            return val != null ? Double.parseDouble(val) : 0.0;
        } catch (Exception e) {
            return 0.0;
        }
    }

    private String dailyKey(Long campaignId) {
        return KEY_PREFIX + campaignId + ":" + LocalDate.now();
    }
}
