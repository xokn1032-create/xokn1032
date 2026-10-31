// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface ISSVNetwork {
    struct Cluster {
        uint32 validatorCount;
        uint64 netMultiplier;
        address owner;
        int256 balance;
        uint64 activeBalance;
        uint64 index;
        bool active;
    }

    function migrateClusterToETH(uint64[] calldata operatorIds, Cluster calldata cluster) external;
}

interface ISSVNetworkViews {
    function getCluster(uint64[] calldata operatorIds, address owner) external view returns (ISSVNetwork.Cluster memory);
}

contract MigrateLiquidatedClusterTest is Test {
    ISSVNetwork ssvNetwork = ISSVNetwork(0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1);
    IERC20 ssvToken = IERC20(0x9D65fF81a3c488d585bBfb0Bfe3c7707c7917f54);
    ISSVNetworkViews ssvViews = ISSVNetworkViews(0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4);

    function setUp() public {
        vm.createSelectFork("mainnet", 26_305_727);
    }

    function test_MigrateLiquidatedClusterSSVWipe() public {
        address clusterOwner = address(0xe2758939507b35A22C30039A7da04B54cdc4C9A0);
        
        // Populate with the exact operator IDs from the event log[span_3](start_span)[span_3](end_span)
        uint64[] memory operatorIds = new uint64[](4);
        operatorIds[0] = 1647;
        operatorIds[1] = 1648;
        operatorIds[2] = 1649;
        operatorIds[3] = 1650;

        // Fetch live cluster struct from the Views contract on the fork
        ISSVNetwork.Cluster memory cluster = ssvViews.getCluster(operatorIds, clusterOwner);

        vm.startPrank(clusterOwner);
        
        uint256 before = ssvToken.balanceOf(clusterOwner);
        console2.log("SSV Balance Before Migration:", before);
        
        // Execute migration logic
        ssvNetwork.migrateClusterToETH(operatorIds, cluster);
        
        uint256 afterBal = ssvToken.balanceOf(clusterOwner);
        console2.log("SSV Balance After Migration:", afterBal);
        console2.log("Lost / Wiped Amount:", before > afterBal ? before - afterBal : 0);

        assertGt(before, 0, "Sanity: cluster should have had SSV balance before migrating");

        vm.stopPrank();
    }
}
